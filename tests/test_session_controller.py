from __future__ import annotations

import threading
import time
from queue import Queue

from auto_paste import FocusState
from errors import ASR_PROTOCOL_ERROR
from models import (
    AudioFrame,
    PasteResult,
    RecognitionEvent,
    RecognitionKind,
    SessionMode,
    SessionState,
)
from session_controller import SessionController


class FakeRecorder:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False
        self.queue: Queue[AudioFrame | None] | None = None

    def start(self, audio_queue: Queue[AudioFrame | None]) -> None:
        self.started = True
        self.queue = audio_queue

    def stop(self) -> None:
        self.stopped = True
        if self.queue is not None:
            try:
                self.queue.put_nowait(None)
            except Exception:
                pass


class FakeRecognizer:
    def __init__(self) -> None:
        self.on_event = None
        self.started = False
        self.stopped = False
        self.events_seen = 0

    def start(self, audio_queue, on_event) -> None:  # noqa: ANN001
        self.started = True
        self.on_event = on_event

    def stop(self) -> None:
        self.stopped = True

    def emit(self, event: RecognitionEvent) -> None:
        assert self.on_event is not None
        self.events_seen += 1
        self.on_event(event)

    def get_health_snapshot(self) -> dict:
        return {
            "thread_alive": self.started and not self.stopped,
            "queue_backlog": 0,
            "connection_active": self.started and not self.stopped,
            "events_seen": self.events_seen,
        }


class FakePasteService:
    def __init__(self, success: bool = True) -> None:
        self.success = success
        self.calls: list[str] = []

    def paste_text(self, text: str, target_app=None) -> PasteResult:  # noqa: ANN001
        self.calls.append(text)
        if self.success:
            return PasteResult(success=True, reason="ok", clipboard_restored=True)
        return PasteResult(success=False, reason="no target", clipboard_restored=True)


class FakeLLMAdapter:
    def __init__(self, should_fail: bool = False) -> None:
        self.should_fail = should_fail
        self.calls: list[str] = []

    def polish_text(self, text: str) -> str:
        self.calls.append(text)
        if self.should_fail:
            raise RuntimeError("llm unavailable")
        return f"1. {text}"


class FakeHistoryLogger:
    def __init__(self) -> None:
        self.records: list[dict] = []

    def append_record(self, **kwargs) -> None:  # noqa: ANN003
        self.records.append(kwargs)


def _emit_final_async(recognizer: FakeRecognizer, text: str) -> None:
    def _emit() -> None:
        time.sleep(0.05)
        recognizer.emit(RecognitionEvent(kind=RecognitionKind.FINAL.value, text=text))

    threading.Thread(target=_emit, daemon=True).start()


def test_raw_happy_path_transitions_to_idle() -> None:
    recorder = FakeRecorder()
    recognizer = FakeRecognizer()
    paste = FakePasteService(success=True)
    history = FakeHistoryLogger()
    transitions: list[tuple[SessionState, SessionState]] = []

    controller = SessionController(
        recorder=recorder,
        recognizer=recognizer,
        paste_service=paste,
        history_logger=history,
        finalize_timeout_s=1.0,
        on_state_change=lambda f, t: transitions.append((f, t)),
    )

    controller.start_session(mode=SessionMode.RAW)
    _emit_final_async(recognizer, "hello")
    controller.stop_session()

    assert recorder.started is True
    assert recorder.stopped is True
    assert recognizer.started is True
    assert paste.calls == ["hello"]
    assert controller.state == SessionState.IDLE
    assert (SessionState.RECORDING, SessionState.FINALIZING_GRACE) in transitions
    assert (SessionState.FINALIZING_GRACE, SessionState.RECOGNIZING_DRAIN) in transitions
    assert (SessionState.PASTING, SessionState.IDLE) in transitions

    assert len(history.records) == 1
    assert history.records[0]["mode"] == SessionMode.RAW
    assert history.records[0]["raw_text"] == "hello"
    assert history.records[0]["polished_text"] is None


def test_polish_success_path_uses_llm_text() -> None:
    recorder = FakeRecorder()
    recognizer = FakeRecognizer()
    paste = FakePasteService(success=True)
    llm = FakeLLMAdapter(should_fail=False)
    history = FakeHistoryLogger()
    transitions: list[tuple[SessionState, SessionState]] = []

    controller = SessionController(
        recorder=recorder,
        recognizer=recognizer,
        paste_service=paste,
        llm_adapter=llm,
        history_logger=history,
        finalize_timeout_s=1.0,
        on_state_change=lambda f, t: transitions.append((f, t)),
    )

    controller.start_session(mode=SessionMode.POLISH)
    _emit_final_async(recognizer, "你好世界")
    controller.stop_session()

    assert llm.calls == ["你好世界"]
    assert paste.calls == ["1. 你好世界"]
    assert (SessionState.RECOGNIZING_DRAIN, SessionState.LLM_PROCESSING) in transitions
    assert (SessionState.LLM_PROCESSING, SessionState.PASTING) in transitions

    assert len(history.records) == 1
    assert history.records[0]["mode"] == SessionMode.POLISH
    assert history.records[0]["polished_text"] == "1. 你好世界"
    assert history.records[0]["llm_error"] is None


def test_polish_failure_falls_back_to_raw_and_emits_error() -> None:
    recorder = FakeRecorder()
    recognizer = FakeRecognizer()
    paste = FakePasteService(success=True)
    llm = FakeLLMAdapter(should_fail=True)
    history = FakeHistoryLogger()
    errors: list[tuple[str, str]] = []

    controller = SessionController(
        recorder=recorder,
        recognizer=recognizer,
        paste_service=paste,
        llm_adapter=llm,
        history_logger=history,
        finalize_timeout_s=1.0,
        on_error=lambda c, m: errors.append((c, m)),
    )

    controller.start_session(mode=SessionMode.POLISH)
    _emit_final_async(recognizer, "原文")
    controller.stop_session()

    assert llm.calls == ["原文"]
    assert paste.calls == ["原文"]
    assert errors
    assert errors[0][0] == ASR_PROTOCOL_ERROR
    assert "LLM polish failed" in errors[0][1]

    assert len(history.records) == 1
    assert history.records[0]["polished_text"] is None
    assert history.records[0]["llm_error"] == "llm unavailable"


def test_history_written_even_when_paste_fails() -> None:
    recorder = FakeRecorder()
    recognizer = FakeRecognizer()
    paste = FakePasteService(success=False)
    history = FakeHistoryLogger()

    controller = SessionController(
        recorder=recorder,
        recognizer=recognizer,
        paste_service=paste,
        history_logger=history,
        finalize_timeout_s=1.0,
    )

    controller.start_session(mode=SessionMode.RAW)
    _emit_final_async(recognizer, "hello")
    controller.stop_session()

    assert paste.calls == ["hello"]
    assert len(history.records) == 1
    result = history.records[0]["paste_result"]
    assert isinstance(result, PasteResult)
    assert result.success is False


def test_stop_timeout_goes_to_error_then_idle() -> None:
    recorder = FakeRecorder()
    recognizer = FakeRecognizer()
    paste = FakePasteService(success=True)
    errors: list[tuple[str, str]] = []

    controller = SessionController(
        recorder=recorder,
        recognizer=recognizer,
        paste_service=paste,
        finalizing_grace_s=0.01,
        finalize_timeout_s=0.05,
        on_error=lambda c, m: errors.append((c, m)),
    )
    controller.start_session()
    controller.stop_session()

    assert controller.state == SessionState.IDLE
    assert errors
    assert errors[0][0] == ASR_PROTOCOL_ERROR


def test_recognizer_error_event_cleans_up() -> None:
    recorder = FakeRecorder()
    recognizer = FakeRecognizer()
    paste = FakePasteService(success=True)
    errors: list[tuple[str, str]] = []

    controller = SessionController(
        recorder=recorder,
        recognizer=recognizer,
        paste_service=paste,
        on_error=lambda c, m: errors.append((c, m)),
    )

    controller.start_session()
    recognizer.emit(
        RecognitionEvent(
            kind=RecognitionKind.ERROR.value,
            code=ASR_PROTOCOL_ERROR,
            message="boom",
            retryable=True,
        )
    )

    assert controller.state == SessionState.IDLE
    assert recorder.stopped is True
    assert recognizer.stopped is True
    assert errors == [(ASR_PROTOCOL_ERROR, "boom")]


def test_start_stop_idempotent() -> None:
    recorder = FakeRecorder()
    recognizer = FakeRecognizer()
    paste = FakePasteService(success=True)

    controller = SessionController(
        recorder=recorder,
        recognizer=recognizer,
        paste_service=paste,
        finalizing_grace_s=0.01,
        finalize_timeout_s=0.05,
    )

    controller.start_session()
    controller.start_session()  # should be no-op
    controller.stop_session()
    controller.stop_session()  # should be no-op

    assert controller.state == SessionState.IDLE


def test_cancel_session_cleans_up_from_recording() -> None:
    recorder = FakeRecorder()
    recognizer = FakeRecognizer()
    paste = FakePasteService(success=True)
    errors: list[tuple[str, str]] = []

    controller = SessionController(
        recorder=recorder,
        recognizer=recognizer,
        paste_service=paste,
        on_error=lambda c, m: errors.append((c, m)),
    )

    controller.start_session()
    assert controller.state == SessionState.RECORDING

    controller.cancel_session("test cancel")

    assert controller.state == SessionState.IDLE
    assert recorder.stopped is True
    assert recognizer.stopped is True
    assert len(errors) == 1
    assert errors[0][1] == "test cancel"


def test_cancel_session_from_idle_is_noop() -> None:
    recorder = FakeRecorder()
    recognizer = FakeRecognizer()
    paste = FakePasteService(success=True)

    controller = SessionController(
        recorder=recorder,
        recognizer=recognizer,
        paste_service=paste,
    )

    controller.cancel_session("noop")  # should not raise
    assert controller.state == SessionState.IDLE


def test_partial_callback_is_invoked() -> None:
    recorder = FakeRecorder()
    recognizer = FakeRecognizer()
    paste = FakePasteService(success=True)
    partials: list[str] = []

    controller = SessionController(
        recorder=recorder,
        recognizer=recognizer,
        paste_service=paste,
        finalize_timeout_s=1.0,
        on_partial=lambda t: partials.append(t),
    )

    controller.start_session()
    recognizer.emit(RecognitionEvent(kind=RecognitionKind.PARTIAL.value, text="hello"))
    recognizer.emit(RecognitionEvent(kind=RecognitionKind.PARTIAL.value, text="hello world"))

    assert partials == ["hello", "hello world"]

    _emit_final_async(recognizer, "hello world")
    controller.stop_session()
    assert controller.state == SessionState.IDLE


def test_empty_final_result_skips_paste() -> None:
    recorder = FakeRecorder()
    recognizer = FakeRecognizer()
    paste = FakePasteService(success=True)

    controller = SessionController(
        recorder=recorder,
        recognizer=recognizer,
        paste_service=paste,
        finalize_timeout_s=1.0,
    )

    controller.start_session()
    _emit_final_async(recognizer, "   ")
    controller.stop_session()

    assert controller.state == SessionState.IDLE
    assert paste.calls == []


def test_state_path_contains_finalizing_grace_and_drain() -> None:
    recorder = FakeRecorder()
    recognizer = FakeRecognizer()
    paste = FakePasteService(success=True)
    transitions: list[tuple[SessionState, SessionState]] = []

    controller = SessionController(
        recorder=recorder,
        recognizer=recognizer,
        paste_service=paste,
        finalize_timeout_s=1.0,
        on_state_change=lambda f, t: transitions.append((f, t)),
    )

    controller.start_session(mode=SessionMode.RAW)
    _emit_final_async(recognizer, "transition check")
    controller.stop_session()

    assert (SessionState.RECORDING, SessionState.FINALIZING_GRACE) in transitions
    assert (SessionState.FINALIZING_GRACE, SessionState.RECOGNIZING_DRAIN) in transitions
    assert (SessionState.RECOGNIZING_DRAIN, SessionState.PASTING) in transitions


def test_missing_final_falls_back_to_partial_text() -> None:
    recorder = FakeRecorder()
    recognizer = FakeRecognizer()
    paste = FakePasteService(success=True)
    errors: list[tuple[str, str]] = []

    controller = SessionController(
        recorder=recorder,
        recognizer=recognizer,
        paste_service=paste,
        finalizing_grace_s=0.01,
        finalize_timeout_s=0.05,
        on_error=lambda c, m: errors.append((c, m)),
    )

    controller.start_session(mode=SessionMode.RAW)
    recognizer.emit(RecognitionEvent(kind=RecognitionKind.PARTIAL.value, text="先说上半句"))
    recognizer.emit(RecognitionEvent(kind=RecognitionKind.PARTIAL.value, text="先说上半句，再补后半句"))
    controller.stop_session()

    assert controller.state == SessionState.IDLE
    assert paste.calls == ["先说上半句，再补后半句"]
    assert errors == []


def test_fast_finalize_path_uses_partial_without_waiting_full_timeout() -> None:
    recorder = FakeRecorder()
    recognizer = FakeRecognizer()
    paste = FakePasteService(success=True)

    controller = SessionController(
        recorder=recorder,
        recognizer=recognizer,
        paste_service=paste,
        finalizing_grace_s=0.01,
        finalize_timeout_s=0.8,
        fast_finalize_quiet_window_s=0.02,
    )

    controller.start_session(mode=SessionMode.RAW)
    recognizer.emit(RecognitionEvent(kind=RecognitionKind.PARTIAL.value, text="快收尾文本"))

    started = time.monotonic()
    controller.stop_session()
    elapsed_s = time.monotonic() - started
    snapshot = controller.get_health_snapshot()

    assert controller.state == SessionState.IDLE
    assert paste.calls == ["快收尾文本"]
    assert snapshot["fast_finalize_hit_count"] >= 1
    assert snapshot["last_finalize_latency_ms"] > 0
    assert elapsed_s < 0.4


def test_drain_waits_tail_window_to_collect_late_final_segment() -> None:
    recorder = FakeRecorder()
    recognizer = FakeRecognizer()
    paste = FakePasteService(success=True)

    controller = SessionController(
        recorder=recorder,
        recognizer=recognizer,
        paste_service=paste,
        finalizing_grace_s=0.01,
        finalize_timeout_s=0.6,
        final_tail_quiet_window_s=0.08,
    )

    controller.start_session(mode=SessionMode.RAW)

    def _emit_two_finals() -> None:
        time.sleep(0.02)
        recognizer.emit(RecognitionEvent(kind=RecognitionKind.FINAL.value, text="前半句"))
        # Simulate late trailing FINAL packet in same session.
        time.sleep(0.03)
        recognizer.emit(RecognitionEvent(kind=RecognitionKind.FINAL.value, text="后半句"))

    threading.Thread(target=_emit_two_finals, daemon=True).start()
    controller.stop_session()

    assert controller.state == SessionState.IDLE
    assert paste.calls == ["前半句后半句"]


def test_retryable_error_triggers_soft_reconnect() -> None:
    recorder = FakeRecorder()
    recognizer_primary = FakeRecognizer()
    recognizer_recovered = FakeRecognizer()
    paste = FakePasteService(success=True)

    def _recognizer_factory() -> FakeRecognizer:
        return recognizer_recovered

    controller = SessionController(
        recorder=recorder,
        recognizer=recognizer_primary,
        paste_service=paste,
        recognizer_factory=_recognizer_factory,
    )

    controller.start_session()
    recognizer_primary.emit(
        RecognitionEvent(
            kind=RecognitionKind.ERROR.value,
            code=ASR_PROTOCOL_ERROR,
            message="retryable failure",
            retryable=True,
        )
    )

    assert controller.state == SessionState.IDLE
    assert controller._recognizer is recognizer_recovered  # noqa: SLF001


def test_retryable_error_triggers_hard_reset_after_threshold() -> None:
    recorder_primary = FakeRecorder()
    recorder_recovered = FakeRecorder()
    recognizer_primary = FakeRecognizer()
    recognizer_recovered = FakeRecognizer()
    paste = FakePasteService(success=True)

    def _recognizer_factory() -> FakeRecognizer:
        return recognizer_recovered

    def _recorder_factory() -> FakeRecorder:
        return recorder_recovered

    controller = SessionController(
        recorder=recorder_primary,
        recognizer=recognizer_primary,
        paste_service=paste,
        recognizer_factory=_recognizer_factory,
        recorder_factory=_recorder_factory,
        recover_hard_reset_threshold=1,
    )

    controller.start_session()
    recognizer_primary.emit(
        RecognitionEvent(
            kind=RecognitionKind.ERROR.value,
            code=ASR_PROTOCOL_ERROR,
            message="force hard reset",
            retryable=True,
        )
    )

    assert controller.state == SessionState.IDLE
    assert controller._recognizer is recognizer_recovered  # noqa: SLF001
    assert controller._recorder is recorder_recovered  # noqa: SLF001


def test_health_snapshot_contains_probe_fields() -> None:
    recorder = FakeRecorder()
    recognizer = FakeRecognizer()
    paste = FakePasteService(success=True)

    controller = SessionController(
        recorder=recorder,
        recognizer=recognizer,
        paste_service=paste,
        finalizing_grace_s=0.01,
        finalize_timeout_s=0.05,
    )

    controller.start_session(mode=SessionMode.RAW)
    recognizer.emit(RecognitionEvent(kind=RecognitionKind.PARTIAL.value, text="probe text"))
    snapshot = controller.get_health_snapshot()

    assert snapshot["state"] == SessionState.RECORDING.value
    assert snapshot["mode"] == SessionMode.RAW.value
    assert isinstance(snapshot["queue_backlog"], int)
    assert snapshot["last_recognition_event_ms"] > 0
    assert isinstance(snapshot["last_finalize_latency_ms"], int)
    assert isinstance(snapshot["fast_finalize_hit_count"], int)
    assert isinstance(snapshot["recognizer"], dict)
    assert snapshot["recognizer"]["events_seen"] >= 1
    assert isinstance(snapshot["recent_events"], list)
    assert len(snapshot["recent_events"]) >= 1
    assert "focus_status" in snapshot
    assert "focus_message" in snapshot
    assert "focus_app_name" in snapshot

    controller.cancel_session("done")


def test_start_session_probes_focus_and_emits_hint(monkeypatch) -> None:  # noqa: ANN001
    recorder = FakeRecorder()
    recognizer = FakeRecognizer()
    paste = FakePasteService(success=True)
    hints: list[tuple[str, str]] = []

    monkeypatch.setattr(
        "session_controller.detect_input_focus_state",
        lambda target_app=None: FocusState(
            status="not_editable",
            app_name="Finder",
            message="当前未聚焦输入框",
        ),
    )

    controller = SessionController(
        recorder=recorder,
        recognizer=recognizer,
        paste_service=paste,
        on_focus_hint=lambda status, message: hints.append((status, message)),
    )

    controller.start_session(mode=SessionMode.RAW)
    snapshot = controller.get_health_snapshot()
    controller.cancel_session("done")

    assert hints
    assert hints[0][0] == "not_editable"
    assert "剪贴板" in hints[0][1]
    assert snapshot["focus_status"] == "not_editable"
    assert snapshot["focus_app_name"] == "Finder"


def test_focus_probe_failure_falls_back_unknown(monkeypatch) -> None:  # noqa: ANN001
    recorder = FakeRecorder()
    recognizer = FakeRecognizer()
    paste = FakePasteService(success=True)

    def _raise_probe(*args, **kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("ax unavailable")

    monkeypatch.setattr("session_controller.detect_input_focus_state", _raise_probe)

    controller = SessionController(
        recorder=recorder,
        recognizer=recognizer,
        paste_service=paste,
    )

    controller.start_session(mode=SessionMode.RAW)
    snapshot = controller.get_health_snapshot()
    controller.cancel_session("done")

    assert snapshot["focus_status"] == "unknown"
    assert "可继续识别" in str(snapshot["focus_message"])


def test_failure_snapshot_keeps_recent_events() -> None:
    recorder = FakeRecorder()
    recognizer = FakeRecognizer()
    paste = FakePasteService(success=True)
    errors: list[tuple[str, str]] = []

    controller = SessionController(
        recorder=recorder,
        recognizer=recognizer,
        paste_service=paste,
        on_error=lambda c, m: errors.append((c, m)),
    )

    controller.start_session(mode=SessionMode.RAW)
    recognizer.emit(RecognitionEvent(kind=RecognitionKind.PARTIAL.value, text="before failure"))
    recognizer.emit(
        RecognitionEvent(
            kind=RecognitionKind.ERROR.value,
            code=ASR_PROTOCOL_ERROR,
            message="boom",
            retryable=True,
        )
    )

    snapshot = controller.get_health_snapshot()
    kinds = [e["kind"] for e in snapshot["recent_events"]]

    assert controller.state == SessionState.IDLE
    assert errors == [(ASR_PROTOCOL_ERROR, "boom")]
    assert "session_failed" in kinds


def test_stale_event_from_previous_session_is_dropped() -> None:
    recorder = FakeRecorder()
    recognizer = FakeRecognizer()
    paste = FakePasteService(success=True)

    controller = SessionController(
        recorder=recorder,
        recognizer=recognizer,
        paste_service=paste,
        finalizing_grace_s=0.01,
        finalize_timeout_s=0.05,
    )

    # Session 1
    controller.start_session(mode=SessionMode.RAW)
    stale_callback = recognizer.on_event
    recognizer.emit(RecognitionEvent(kind=RecognitionKind.FINAL.value, text="第一段"))
    controller.stop_session()

    # Session 2
    controller.start_session(mode=SessionMode.RAW)
    assert stale_callback is not None
    stale_callback(RecognitionEvent(kind=RecognitionKind.FINAL.value, text="旧会话尾包"))
    recognizer.emit(RecognitionEvent(kind=RecognitionKind.FINAL.value, text="第二段"))
    controller.stop_session()

    snapshot = controller.get_health_snapshot()

    assert paste.calls == ["第一段", "第二段"]
    assert snapshot["stale_event_drop_count"] >= 1


def test_freeze_snapshot_blocks_late_events_during_paste() -> None:
    class _LateEventPasteService(FakePasteService):
        def __init__(self, recognizer_ref: FakeRecognizer) -> None:
            super().__init__(success=True)
            self._recognizer_ref = recognizer_ref

        def paste_text(self, text: str, target_app=None) -> PasteResult:  # noqa: ANN001
            # Simulate delayed event arriving after snapshot freeze.
            self._recognizer_ref.emit(
                RecognitionEvent(kind=RecognitionKind.FINAL.value, text="迟到尾包")
            )
            return super().paste_text(text, target_app=target_app)

    recorder = FakeRecorder()
    recognizer = FakeRecognizer()
    paste = _LateEventPasteService(recognizer)

    controller = SessionController(
        recorder=recorder,
        recognizer=recognizer,
        paste_service=paste,
        finalizing_grace_s=0.01,
        finalize_timeout_s=0.05,
    )

    controller.start_session(mode=SessionMode.RAW)
    recognizer.emit(RecognitionEvent(kind=RecognitionKind.FINAL.value, text="主文本"))
    controller.stop_session()

    snapshot = controller.get_health_snapshot()
    assert paste.calls == ["主文本"]
    assert snapshot["stale_event_drop_count"] >= 1
