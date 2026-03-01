"""State-machine based session orchestration."""

from __future__ import annotations

from collections import deque
import json
import logging
import threading
import time
from queue import Queue
from typing import Callable, Optional

from auto_paste import detect_input_focus_state, get_frontmost_app
from errors import ASR_PROTOCOL_ERROR, AUTH_FAILED, NO_ACTIVE_TARGET, NETWORK_ERROR
from interfaces import HistoryLogger, LLMAdapter, PasteService, Recorder, RecognizerAdapter
from models import (
    AudioFrame,
    PasteResult,
    RecognitionEvent,
    RecognitionKind,
    SessionMode,
    SessionState,
)
from transcript_aggregator import TranscriptAggregator

log = logging.getLogger(__name__)

StateCallback = Callable[[SessionState, SessionState], None]
PartialCallback = Callable[[str], None]
ErrorCallback = Callable[[str, str], None]
FocusHintCallback = Callable[[str, str], None]


class SessionController:
    def __init__(
        self,
        recorder: Recorder,
        recognizer: RecognizerAdapter,
        paste_service: PasteService,
        llm_adapter: LLMAdapter | None = None,
        history_logger: HistoryLogger | None = None,
        recognizer_factory: Callable[[], RecognizerAdapter] | None = None,
        recorder_factory: Callable[[], Recorder] | None = None,
        recover_hard_reset_threshold: int = 3,
        finalize_timeout_s: float = 5.0,
        finalizing_grace_s: float = 1.2,
        final_tail_quiet_window_s: float = 0.35,
        fast_finalize_quiet_window_s: float = 0.5,
        enable_fast_finalize: bool = True,
        drain_timeout_s: float | None = None,
        queue_maxsize: int = 50,
        on_state_change: Optional[StateCallback] = None,
        on_partial: Optional[PartialCallback] = None,
        on_error: Optional[ErrorCallback] = None,
        on_focus_hint: Optional[FocusHintCallback] = None,
    ) -> None:
        self._recorder = recorder
        self._recognizer = recognizer
        self._paste_service = paste_service
        self._llm_adapter = llm_adapter
        self._history_logger = history_logger
        self._recognizer_factory = recognizer_factory
        self._recorder_factory = recorder_factory
        self._recover_hard_reset_threshold = max(1, recover_hard_reset_threshold)
        self._finalize_timeout_s = finalize_timeout_s
        self._finalizing_grace_s = max(0.0, finalizing_grace_s)
        self._final_tail_quiet_window_ms = int(max(0.0, final_tail_quiet_window_s) * 1000)
        self._fast_finalize_quiet_window_ms = int(max(0.0, fast_finalize_quiet_window_s) * 1000)
        self._enable_fast_finalize = enable_fast_finalize
        self._drain_timeout_s = (
            max(0.0, drain_timeout_s)
            if drain_timeout_s is not None
            else max(0.0, finalize_timeout_s)
        )
        self._on_state_change = on_state_change
        self._on_partial = on_partial
        self._on_error = on_error
        self._on_focus_hint = on_focus_hint

        self._lock = threading.RLock()
        self._state = SessionState.IDLE
        self._mode = SessionMode.RAW
        self._session_id = 0
        self._session_token = ""
        self._active_event_token = ""
        self._accept_recognition_events = False
        self._stale_event_drop_count = 0
        self._audio_queue: Queue[AudioFrame | None] = Queue(maxsize=queue_maxsize)
        self._final_event = threading.Event()
        self._final_text = ""
        self._transcript = TranscriptAggregator()
        self._target_app = None  # App that had focus when recording started
        self._focus_status = "unknown"
        self._focus_message = ""
        self._focus_app_name = ""
        self._finalizing_started_at_ms = 0
        self._drain_started_at_ms = 0
        self._last_recognition_event_ms = 0
        self._last_finalize_latency_ms = 0
        self._fast_finalize_hit_count = 0
        self._recover_consecutive_failures = 0
        self._recent_events: deque[dict[str, object]] = deque(maxlen=20)

    @property
    def state(self) -> SessionState:
        return self._state

    def get_health_snapshot(self) -> dict[str, object]:
        return {
            "session_id": self._session_id,
            "session_token": self._session_token,
            "state": self._state.value,
            "mode": self._mode.value,
            "queue_backlog": self._queue_backlog(),
            "last_recognition_event_ms": self._last_recognition_event_ms,
            "last_finalize_latency_ms": self._last_finalize_latency_ms,
            "fast_finalize_hit_count": self._fast_finalize_hit_count,
            "recover_consecutive_failures": self._recover_consecutive_failures,
            "stale_event_drop_count": self._stale_event_drop_count,
            "focus_status": self._focus_status,
            "focus_message": self._focus_message,
            "focus_app_name": self._focus_app_name,
            "recognizer": self._recognizer_health_snapshot(),
            "recent_events": list(self._recent_events),
        }

    def start_session(self, mode: SessionMode = SessionMode.RAW) -> None:
        with self._lock:
            if self._state != SessionState.IDLE:
                log.warning("start_session ignored: state=%s", self._state)
                return
            self._session_id += 1
            self._session_token = self._new_session_token()
            self._active_event_token = self._session_token
            self._accept_recognition_events = True
            self._mode = mode
            self._final_event.clear()
            self._final_text = ""
            self._transcript.reset()
            self._recent_events.clear()
            self._finalizing_started_at_ms = 0
            self._drain_started_at_ms = 0
            self._last_recognition_event_ms = 0
            self._last_finalize_latency_ms = 0
            self._audio_queue = Queue(maxsize=self._audio_queue.maxsize)
            self._target_app = get_frontmost_app()  # Save focused app
            self._focus_status = "unknown"
            self._focus_message = ""
            self._focus_app_name = ""
            self._probe_focus_state()
            log.info(
                "Target app: %s mode=%s focus=%s",
                self._target_app.localizedName() if self._target_app else "unknown",
                mode.value,
                self._focus_status,
            )
            self._transition(SessionState.RECORDING)
            try:
                log.info("Starting recognizer...")
                self._recognizer.start(
                    self._audio_queue,
                    self._bind_session_event_handler(self._session_token),
                )
                log.info("Starting recorder...")
                self._recorder.start(self._audio_queue)
                log.info("Session started successfully")
                self._log_health("session_started")
            except Exception as exc:
                log.error("start failed: %s", exc, exc_info=True)
                self._fail(ASR_PROTOCOL_ERROR, f"start failed: {exc}")

    def stop_session(self) -> None:
        with self._lock:
            if self._state != SessionState.RECORDING:
                return
            self._finalizing_started_at_ms = now_ms()
            self._transition(SessionState.FINALIZING_GRACE)
            self._safe_stop_recorder()

        # Grace window: allow trailing recognition events after key release.
        self._final_event.wait(timeout=self._finalizing_grace_s)

        with self._lock:
            if self._state != SessionState.FINALIZING_GRACE:
                return
            self._drain_started_at_ms = now_ms()
            self._transition(SessionState.RECOGNIZING_DRAIN)
            self._push_recent_event(
                "drain_started",
                {"grace_s": self._finalizing_grace_s, "drain_s": self._drain_timeout_s},
            )

        got_final, fast_finalize_hit = self._wait_final_or_fast_finalize()

        with self._lock:
            if self._state != SessionState.RECOGNIZING_DRAIN:
                return
            self._freeze_event_stream_for_current_session()
            if fast_finalize_hit:
                self._fast_finalize_hit_count += 1
                self._push_recent_event(
                    "fast_finalize_hit",
                    {"quiet_window_ms": self._fast_finalize_quiet_window_ms},
                )
            if not got_final:
                # Fallback: keep user output flow even when FINAL is missing.
                self._final_text = self._transcript.best_text()
                fallback_kind = (
                    "final_missing_use_fast_fallback"
                    if fast_finalize_hit
                    else "final_missing_use_fallback"
                )
                self._push_recent_event(fallback_kind)
            raw_text = self._final_text.strip()
            if not raw_text and not got_final:
                self._fail(
                    ASR_PROTOCOL_ERROR,
                    (
                        "final result timeout "
                        f"(grace={self._finalizing_grace_s:.2f}s, "
                        f"drain={self._drain_timeout_s:.2f}s)"
                    ),
                )
                return
            if not raw_text:
                self._transition(SessionState.IDLE)
                self._safe_stop_recognizer()
                return

            paste_text = raw_text
            polished_text: str | None = None
            llm_error: str | None = None

            if self._mode == SessionMode.POLISH:
                self._transition(SessionState.LLM_PROCESSING)
                try:
                    polished_text = self._run_llm(raw_text)
                    paste_text = polished_text
                except Exception as exc:
                    llm_error = str(exc)
                    self._emit_error(ASR_PROTOCOL_ERROR, f"LLM polish failed: {llm_error}")
                    paste_text = raw_text

            self._transition(SessionState.PASTING)
            result = self._run_paste(paste_text)
            if not result.success:
                self._emit_error(NO_ACTIVE_TARGET, result.reason)

            self._append_history(
                mode=self._mode,
                raw_text=raw_text,
                polished_text=polished_text,
                llm_error=llm_error,
                paste_result=result,
            )

            self._safe_stop_recognizer()
            self._recover_consecutive_failures = 0
            if self._finalizing_started_at_ms > 0:
                self._last_finalize_latency_ms = now_ms() - self._finalizing_started_at_ms
            self._log_health("session_completed")
            self._transition(SessionState.IDLE)

    def cancel_session(self, reason: str) -> None:
        with self._lock:
            if self._state == SessionState.IDLE:
                return
            self._emit_error(ASR_PROTOCOL_ERROR, reason)
            self._freeze_event_stream_for_current_session()
            self._safe_stop_recorder()
            self._safe_stop_recognizer()
            self._transition(SessionState.IDLE)

    def replace_recognizer(self, recognizer: RecognizerAdapter) -> None:
        with self._lock:
            if self._state != SessionState.IDLE:
                raise RuntimeError("cannot replace recognizer while session is active")
            self._safe_stop_recognizer()
            self._recognizer = recognizer

    def replace_llm_adapter(self, llm_adapter: LLMAdapter) -> None:
        with self._lock:
            if self._state != SessionState.IDLE:
                raise RuntimeError("cannot replace llm adapter while session is active")
            self._llm_adapter = llm_adapter

    def _run_paste(self, text: str) -> PasteResult:
        try:
            return self._paste_service.paste_text(text, target_app=self._target_app)
        except Exception as exc:  # pragma: no cover - defensive
            return PasteResult(success=False, reason=str(exc), clipboard_restored=False)

    def _probe_focus_state(self) -> None:
        status = "unknown"
        app_name = ""
        message = "无法检测输入焦点（可继续识别）"
        try:
            focus = detect_input_focus_state(target_app=self._target_app)
            status = focus.status or "unknown"
            app_name = focus.app_name or ""
            message = (focus.message or "").strip() or message
            if status == "not_editable" and "剪贴板" not in message:
                message = f"{message}（可继续识别，结果将保留剪贴板）"
            elif status == "unknown" and "可继续识别" not in message:
                message = f"{message}（可继续识别）"
        except Exception as exc:
            log.warning("focus probe failed: %s", exc, exc_info=True)

        self._focus_status = status
        self._focus_app_name = app_name
        self._focus_message = message
        self._push_recent_event(
            "focus_probe",
            {
                "focus_status": status,
                "focus_message": message,
                "focus_app_name": app_name,
            },
        )
        if self._on_focus_hint:
            self._on_focus_hint(status, message)

    def _run_llm(self, text: str) -> str:
        if self._llm_adapter is None:
            raise RuntimeError("LLM adapter not configured")
        polished = self._llm_adapter.polish_text(text)
        if not polished.strip():
            raise RuntimeError("LLM returned empty content")
        return polished.strip()

    def _append_history(
        self,
        *,
        mode: SessionMode,
        raw_text: str,
        polished_text: str | None,
        llm_error: str | None,
        paste_result: PasteResult,
    ) -> None:
        if self._history_logger is None:
            return
        try:
            self._history_logger.append_record(
                mode=mode,
                raw_text=raw_text,
                polished_text=polished_text,
                llm_error=llm_error,
                paste_result=paste_result,
            )
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("history logger failed: %s", exc, exc_info=True)

    def _handle_recognition_event(self, event: RecognitionEvent, event_token: str) -> None:
        with self._lock:
            if not self._accept_recognition_events or event_token != self._active_event_token:
                self._stale_event_drop_count += 1
                self._push_recent_event(
                    "stale_event_dropped",
                    {
                        "event_token": event_token,
                        "active_event_token": self._active_event_token,
                        "kind": event.kind,
                    },
                )
                return
            self._last_recognition_event_ms = now_ms()
            kind = event.kind
            self._push_recent_event(
                "recognition_event",
                {
                    "kind": kind,
                    "code": event.code,
                    "text_len": len(event.text or ""),
                    "event_token": event_token,
                },
            )
            if kind == RecognitionKind.PARTIAL.value:
                self._transcript.on_partial(event.text)
                if self._on_partial:
                    self._on_partial(event.text)
                return
            if kind == RecognitionKind.FINAL.value:
                self._transcript.on_final(event.text)
                self._final_text = self._transcript.final_text()
                self._final_event.set()
                return
            if kind == RecognitionKind.ERROR.value:
                self._fail(event.code or ASR_PROTOCOL_ERROR, event.message)

    def _fail(self, code: str, message: str) -> None:
        log.error("Session FAIL: %s — %s", code, message)
        self._push_recent_event("session_failed", {"error_code": code, "error_message": message})
        self._log_health(
            "session_failed",
            {
                "error_code": code,
                "error_message": message,
                "failure_snapshot": list(self._recent_events),
            },
        )
        self._transition(SessionState.ERROR)
        self._emit_error(code, message)
        self._freeze_event_stream_for_current_session()
        self._safe_stop_recorder()
        self._safe_stop_recognizer()
        self._attempt_recover(code)
        self._transition(SessionState.IDLE)

    def _attempt_recover(self, code: str) -> None:
        if code not in {ASR_PROTOCOL_ERROR, NETWORK_ERROR}:
            if code == AUTH_FAILED:
                self._recover_consecutive_failures = 0
            return
        if self._recognizer_factory is None:
            return

        self._recover_consecutive_failures += 1
        do_hard_reset = (
            self._recover_consecutive_failures >= self._recover_hard_reset_threshold
            and self._recorder_factory is not None
        )

        self._transition(SessionState.RECOVERING)
        mode = "hard-reset" if do_hard_reset else "soft-reconnect"
        recover_started_ms = now_ms()
        log.warning(
            "Attempting session recovery: mode=%s failures=%d",
            mode,
            self._recover_consecutive_failures,
        )
        try:
            self._safe_stop_recognizer()
            self._recognizer = self._recognizer_factory()
            if do_hard_reset and self._recorder_factory is not None:
                self._safe_stop_recorder()
                self._recorder = self._recorder_factory()
            self._recover_consecutive_failures = 0
            recover_cost_ms = now_ms() - recover_started_ms
            log.info("Session recovery succeeded: mode=%s cost_ms=%d", mode, recover_cost_ms)
            self._log_health(
                "recovery_succeeded",
                {"recover_mode": mode, "recover_cost_ms": recover_cost_ms},
            )
        except Exception as exc:
            log.error("Session recovery failed: mode=%s err=%s", mode, exc, exc_info=True)
            self._log_health("recovery_failed", {"recover_mode": mode, "recover_error": str(exc)})

    def _emit_error(self, code: str, message: str) -> None:
        log.warning("Emitting error: %s — %s", code, message)
        if self._on_error:
            self._on_error(code, message)

    def _safe_stop_recorder(self) -> None:
        try:
            self._recorder.stop()
        except Exception as exc:
            log.warning("stop recorder error: %s", exc)

    def _safe_stop_recognizer(self) -> None:
        try:
            self._recognizer.stop()
        except Exception as exc:
            log.warning("stop recognizer error: %s", exc)

    def _wait_final_or_fast_finalize(self) -> tuple[bool, bool]:
        if self._drain_timeout_s <= 0:
            return self._final_event.is_set(), False

        deadline = time.monotonic() + self._drain_timeout_s
        poll_s = min(0.05, self._drain_timeout_s)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return self._final_event.is_set(), False
            with self._lock:
                if self._state != SessionState.RECOGNIZING_DRAIN:
                    return self._final_event.is_set(), False
                if self._final_event.is_set():
                    if self._is_quiet_for_ms_locked(self._final_tail_quiet_window_ms):
                        return True, False
                elif self._should_fast_finalize_locked():
                    return False, True
            time.sleep(min(poll_s, remaining))

    def _should_fast_finalize_locked(self) -> bool:
        if not self._enable_fast_finalize:
            return False
        if self._fast_finalize_quiet_window_ms <= 0:
            return False
        if self._last_recognition_event_ms <= 0:
            return False
        if not self._is_quiet_for_ms_locked(self._fast_finalize_quiet_window_ms):
            return False
        return bool(self._transcript.best_text().strip())

    def _is_quiet_for_ms_locked(self, quiet_window_ms: int) -> bool:
        if quiet_window_ms <= 0:
            return True
        if self._last_recognition_event_ms <= 0:
            return False
        return now_ms() - self._last_recognition_event_ms >= quiet_window_ms

    def _queue_backlog(self) -> int:
        try:
            return int(self._audio_queue.qsize())
        except Exception:
            return 0

    def _recognizer_health_snapshot(self) -> dict[str, object]:
        getter = getattr(self._recognizer, "get_health_snapshot", None)
        if getter is None:
            return {}
        try:
            snapshot = getter()
            return snapshot if isinstance(snapshot, dict) else {}
        except Exception:
            return {}

    def _log_health(self, event: str, extra: dict[str, object] | None = None) -> None:
        payload: dict[str, object] = {
            "event": event,
            "session_id": self._session_id,
            "state": self._state.value,
            "mode": self._mode.value,
            "session_token": self._session_token,
            "queue_backlog": self._queue_backlog(),
            "last_recognition_event_ms": self._last_recognition_event_ms,
            "last_finalize_latency_ms": self._last_finalize_latency_ms,
            "fast_finalize_hit_count": self._fast_finalize_hit_count,
            "recover_consecutive_failures": self._recover_consecutive_failures,
            "stale_event_drop_count": self._stale_event_drop_count,
            "focus_status": self._focus_status,
            "focus_message": self._focus_message,
            "focus_app_name": self._focus_app_name,
            "recognizer": self._recognizer_health_snapshot(),
            "recent_events": list(self._recent_events),
        }
        if extra:
            payload.update(extra)
        log.info("HEALTH %s", json.dumps(payload, ensure_ascii=False, sort_keys=True))

    def _push_recent_event(
        self,
        kind: str,
        payload: dict[str, object] | None = None,
    ) -> None:
        item: dict[str, object] = {
            "ts_ms": now_ms(),
            "kind": kind,
            "state": self._state.value,
        }
        if payload:
            item.update(payload)
        self._recent_events.append(item)

    def _bind_session_event_handler(self, session_token: str) -> Callable[[RecognitionEvent], None]:
        def _handler(event: RecognitionEvent) -> None:
            self._handle_recognition_event(event, session_token)

        return _handler

    def _freeze_event_stream_for_current_session(self) -> None:
        self._accept_recognition_events = False
        self._active_event_token = ""

    def _new_session_token(self) -> str:
        return f"{self._session_id}-{now_ms()}"

    def _transition(self, to_state: SessionState) -> None:
        from_state = self._state
        if from_state == to_state:
            return
        self._push_recent_event(
            "state_transition",
            {"from_state": from_state.value, "to_state": to_state.value},
        )
        log.info("State: %s → %s", from_state.value, to_state.value)
        self._state = to_state
        if self._on_state_change:
            self._on_state_change(from_state, to_state)


def now_ms() -> int:
    return int(time.time() * 1000)
