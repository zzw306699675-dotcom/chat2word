from __future__ import annotations

import threading
import time
from queue import Queue

from errors import ASR_PROTOCOL_ERROR
from models import AudioFrame, PasteResult, RecognitionEvent, RecognitionKind, SessionState
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

    def start(self, audio_queue, on_event) -> None:  # noqa: ANN001
        self.started = True
        self.on_event = on_event

    def stop(self) -> None:
        self.stopped = True

    def emit(self, event: RecognitionEvent) -> None:
        assert self.on_event is not None
        self.on_event(event)


class FakePasteService:
    def __init__(self, success: bool = True) -> None:
        self.success = success
        self.calls: list[str] = []

    def paste_text(self, text: str) -> PasteResult:
        self.calls.append(text)
        if self.success:
            return PasteResult(success=True, reason="ok", clipboard_restored=True)
        return PasteResult(success=False, reason="no target", clipboard_restored=True)


def test_happy_path_transitions_to_idle() -> None:
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

    controller.start_session()

    def emit_final() -> None:
        time.sleep(0.05)
        recognizer.emit(RecognitionEvent(kind=RecognitionKind.FINAL.value, text="hello"))

    threading.Thread(target=emit_final, daemon=True).start()
    controller.stop_session()

    assert recorder.started is True
    assert recorder.stopped is True
    assert recognizer.started is True
    assert paste.calls == ["hello"]
    assert controller.state == SessionState.IDLE
    assert (SessionState.RECORDING, SessionState.FINALIZING) in transitions
    assert (SessionState.PASTING, SessionState.IDLE) in transitions


def test_stop_timeout_goes_to_error_then_idle() -> None:
    recorder = FakeRecorder()
    recognizer = FakeRecognizer()
    paste = FakePasteService(success=True)
    errors: list[tuple[str, str]] = []

    controller = SessionController(
        recorder=recorder,
        recognizer=recognizer,
        paste_service=paste,
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

    # Clean up: emit final so stop doesn't timeout
    def emit_final() -> None:
        time.sleep(0.05)
        recognizer.emit(RecognitionEvent(kind=RecognitionKind.FINAL.value, text="hello world"))

    threading.Thread(target=emit_final, daemon=True).start()
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

    def emit_empty_final() -> None:
        time.sleep(0.05)
        recognizer.emit(RecognitionEvent(kind=RecognitionKind.FINAL.value, text="   "))

    threading.Thread(target=emit_empty_final, daemon=True).start()
    controller.stop_session()

    assert controller.state == SessionState.IDLE
    assert paste.calls == []  # paste was never called
