"""State-machine based session orchestration."""

from __future__ import annotations

import threading
import time
from queue import Queue
from typing import Callable, Optional

from errors import ASR_PROTOCOL_ERROR, NO_ACTIVE_TARGET
from interfaces import PasteService, Recorder, RecognizerAdapter
from models import AudioFrame, PasteResult, RecognitionEvent, RecognitionKind, SessionState

StateCallback = Callable[[SessionState, SessionState], None]
PartialCallback = Callable[[str], None]
ErrorCallback = Callable[[str, str], None]


class SessionController:
    def __init__(
        self,
        recorder: Recorder,
        recognizer: RecognizerAdapter,
        paste_service: PasteService,
        finalize_timeout_s: float = 3.0,
        queue_maxsize: int = 50,
        on_state_change: Optional[StateCallback] = None,
        on_partial: Optional[PartialCallback] = None,
        on_error: Optional[ErrorCallback] = None,
    ) -> None:
        self._recorder = recorder
        self._recognizer = recognizer
        self._paste_service = paste_service
        self._finalize_timeout_s = finalize_timeout_s
        self._on_state_change = on_state_change
        self._on_partial = on_partial
        self._on_error = on_error

        self._lock = threading.RLock()
        self._state = SessionState.IDLE
        self._session_id = 0
        self._audio_queue: Queue[AudioFrame | None] = Queue(maxsize=queue_maxsize)
        self._final_event = threading.Event()
        self._final_text = ""

    @property
    def state(self) -> SessionState:
        return self._state

    def start_session(self) -> None:
        with self._lock:
            if self._state != SessionState.IDLE:
                return
            self._session_id += 1
            self._final_event.clear()
            self._final_text = ""
            self._audio_queue = Queue(maxsize=self._audio_queue.maxsize)
            self._transition(SessionState.RECORDING)
            try:
                self._recognizer.start(self._audio_queue, self._handle_recognition_event)
                self._recorder.start(self._audio_queue)
            except Exception as exc:  # pragma: no cover - defensive
                self._fail(ASR_PROTOCOL_ERROR, f"start failed: {exc}")

    def stop_session(self) -> None:
        with self._lock:
            if self._state != SessionState.RECORDING:
                return
            self._transition(SessionState.FINALIZING)
            self._safe_stop_recorder()

        got_final = self._final_event.wait(timeout=self._finalize_timeout_s)

        with self._lock:
            if self._state != SessionState.FINALIZING:
                return
            if not got_final:
                self._fail(ASR_PROTOCOL_ERROR, "final result timeout")
                return

            final_text = self._final_text.strip()
            if not final_text:
                self._transition(SessionState.IDLE)
                self._safe_stop_recognizer()
                return

            self._transition(SessionState.PASTING)
            result = self._run_paste(final_text)
            if not result.success:
                self._emit_error(NO_ACTIVE_TARGET, result.reason)
            self._safe_stop_recognizer()
            self._transition(SessionState.IDLE)

    def cancel_session(self, reason: str) -> None:
        with self._lock:
            if self._state == SessionState.IDLE:
                return
            self._emit_error(ASR_PROTOCOL_ERROR, reason)
            self._safe_stop_recorder()
            self._safe_stop_recognizer()
            self._transition(SessionState.IDLE)

    def _run_paste(self, text: str) -> PasteResult:
        try:
            return self._paste_service.paste_text(text)
        except Exception as exc:  # pragma: no cover - defensive
            return PasteResult(success=False, reason=str(exc), clipboard_restored=False)

    def _handle_recognition_event(self, event: RecognitionEvent) -> None:
        with self._lock:
            kind = event.kind
            if kind == RecognitionKind.PARTIAL.value and self._on_partial:
                self._on_partial(event.text)
                return
            if kind == RecognitionKind.FINAL.value:
                self._final_text = event.text
                self._final_event.set()
                return
            if kind == RecognitionKind.ERROR.value:
                self._fail(event.code or ASR_PROTOCOL_ERROR, event.message)

    def _fail(self, code: str, message: str) -> None:
        self._transition(SessionState.ERROR)
        self._emit_error(code, message)
        self._safe_stop_recorder()
        self._safe_stop_recognizer()
        self._transition(SessionState.IDLE)

    def _emit_error(self, code: str, message: str) -> None:
        if self._on_error:
            self._on_error(code, message)

    def _safe_stop_recorder(self) -> None:
        try:
            self._recorder.stop()
        except Exception:  # pragma: no cover - defensive
            pass

    def _safe_stop_recognizer(self) -> None:
        try:
            self._recognizer.stop()
        except Exception:  # pragma: no cover - defensive
            pass

    def _transition(self, to_state: SessionState) -> None:
        from_state = self._state
        if from_state == to_state:
            return
        self._state = to_state
        if self._on_state_change:
            self._on_state_change(from_state, to_state)


def now_ms() -> int:
    return int(time.time() * 1000)
