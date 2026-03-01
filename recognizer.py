"""ASR recognizer adapter using DashScope fun-asr-realtime (WebSocket streaming).

Uses dashscope.audio.asr.Recognition for real-time speech recognition via
WebSocket.  Audio frames are forwarded to the service in real-time using
send_audio_frame(), and results are received asynchronously via callbacks.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from queue import Empty, Queue
from typing import Callable, Optional

from errors import ASR_PROTOCOL_ERROR, AUTH_FAILED, NETWORK_ERROR
from models import AudioFrame, RecognitionEvent, RecognitionKind

log = logging.getLogger(__name__)

try:
    import dashscope
    from dashscope.audio.asr import Recognition, RecognitionCallback, RecognitionResult
    _HAS_DASHSCOPE = True
except Exception:  # pragma: no cover
    _HAS_DASHSCOPE = False


class DashscopeRecognizerAdapter:
    def __init__(
        self,
        api_key: str,
        model: str = "fun-asr-realtime",
        sample_rate: int = 16000,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._sample_rate = sample_rate
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._audio_queue: Optional[Queue[AudioFrame | None]] = None
        self._on_event: Optional[Callable[[RecognitionEvent], None]] = None
        self._recognition: Optional[object] = None
        self._last_event_at_ms = 0
        self._last_partial_text = ""
        self._final_emitted = False
        self._exception_count = 0

    def start(
        self,
        audio_queue: Queue[AudioFrame | None],
        on_event: Callable[[RecognitionEvent], None],
    ) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._audio_queue = audio_queue
        self._on_event = on_event
        self._last_event_at_ms = 0
        self._last_partial_text = ""
        self._final_emitted = False
        self._exception_count = 0
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)

    def get_health_snapshot(self) -> dict[str, object]:
        return {
            "thread_alive": bool(self._thread and self._thread.is_alive()),
            "stop_event_set": self._stop_event.is_set(),
            "connection_active": self._recognition is not None,
            "queue_backlog": self._audio_queue.qsize() if self._audio_queue else 0,
            "last_event_at_ms": self._last_event_at_ms,
            "final_emitted": self._final_emitted,
            "last_partial_text": self._last_partial_text,
            "exception_count": self._exception_count,
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _worker(self) -> None:
        """Start Recognition, feed audio frames, then stop."""
        if self._audio_queue is None or self._on_event is None:
            return

        if not _HAS_DASHSCOPE:
            self._on_callback_event(RecognitionEvent(
                kind=RecognitionKind.ERROR.value,
                code=ASR_PROTOCOL_ERROR,
                message="dashscope is not installed",
                retryable=False,
            ))
            return

        api_key = self._api_key or os.getenv("DASHSCOPE_API_KEY", "")
        if not api_key:
            self._on_callback_event(RecognitionEvent(
                kind=RecognitionKind.ERROR.value,
                code=AUTH_FAILED,
                message="No API key configured",
                retryable=False,
            ))
            return

        # Configure dashscope
        dashscope.api_key = api_key
        dashscope.base_websocket_api_url = 'wss://dashscope.aliyuncs.com/api-ws/v1/inference'

        # Build callback
        callback = _ASRCallback(self._on_callback_event)

        try:
            recognition = Recognition(
                model=self._model,
                format='pcm',
                sample_rate=self._sample_rate,
                semantic_punctuation_enabled=False,
                callback=callback,
            )
            self._recognition = recognition
            log.info("Starting Recognition (model=%s, rate=%d)", self._model, self._sample_rate)
            recognition.start()
        except Exception as exc:
            log.error("Recognition start failed: %s", exc, exc_info=True)
            self._on_callback_event(self._to_error_event(exc))
            return

        # Feed audio frames from the queue
        try:
            while not self._stop_event.is_set():
                try:
                    frame = self._audio_queue.get(timeout=0.1)
                except Empty:
                    continue
                if frame is None:  # Sentinel — recording stopped
                    break
                recognition.send_audio_frame(frame.pcm16_bytes)
        except Exception as exc:
            log.error("Error sending audio: %s", exc, exc_info=True)
            self._on_callback_event(self._to_error_event(exc))
            return

        # Stop recognition (blocks until final result is received)
        try:
            log.info("Stopping Recognition (waiting for final result)...")
            recognition.stop()
            log.info("Recognition stopped")
        except Exception as exc:
            log.error("Recognition stop error: %s", exc, exc_info=True)
            # Don't emit error here — the callback may have already emitted the final result

        self._recognition = None

    def _on_callback_event(self, event: RecognitionEvent) -> None:
        self._last_event_at_ms = int(time.time() * 1000)
        if event.kind == RecognitionKind.PARTIAL.value:
            self._last_partial_text = event.text
        elif event.kind == RecognitionKind.FINAL.value:
            self._final_emitted = True
        elif event.kind == RecognitionKind.ERROR.value:
            self._exception_count += 1
        if self._on_event:
            self._on_event(event)

    def _to_error_event(self, exc: Exception) -> RecognitionEvent:
        """Map an SDK/network exception to a standard error event."""
        message = str(exc)
        low = message.lower()
        if "401" in low or "auth" in low or "api key" in low:
            code = AUTH_FAILED
            retryable = False
        elif "timeout" in low or "network" in low or "connection" in low:
            code = NETWORK_ERROR
            retryable = True
        else:
            code = ASR_PROTOCOL_ERROR
            retryable = True
        return RecognitionEvent(
            kind=RecognitionKind.ERROR.value,
            code=code,
            message=message,
            retryable=retryable,
        )


class _ASRCallback(RecognitionCallback):
    """Bridge between dashscope RecognitionCallback and our RecognitionEvent system."""

    def __init__(self, on_event: Callable[[RecognitionEvent], None]) -> None:
        self._on_event = on_event
        self._latest_text = ""
        self._final_emitted = False

    def on_open(self) -> None:
        log.info("ASR WebSocket connected")

    def on_close(self) -> None:
        log.info("ASR WebSocket closed")

    def on_event(self, result: RecognitionResult) -> None:
        sentence = result.get_sentence()
        if not sentence or 'text' not in sentence:
            return
        text = sentence['text']
        self._latest_text = text

        if RecognitionResult.is_sentence_end(sentence):
            log.info("ASR sentence end: %s", text)
            self._final_emitted = True
            self._on_event(RecognitionEvent(
                kind=RecognitionKind.FINAL.value,
                text=text,
            ))
        else:
            log.debug("ASR partial: %s", text)
            self._on_event(RecognitionEvent(
                kind=RecognitionKind.PARTIAL.value,
                text=text,
            ))

    def on_complete(self) -> None:
        log.info("ASR recognition completed")
        # Some edge cases (e.g., long pauses/no valid sentence-end) may not
        # emit a FINAL event. Emit one here to let the session finalize safely.
        if not self._final_emitted:
            self._final_emitted = True
            self._on_event(RecognitionEvent(
                kind=RecognitionKind.FINAL.value,
                text=self._latest_text,
            ))

    def on_error(self, result: RecognitionResult) -> None:
        msg = getattr(result, 'message', str(result))
        # Long silence while holding the key can trigger NO_VALID_AUDIO_ERROR
        # on some streams. Treat it as non-fatal so the session can continue.
        if "NO_VALID_AUDIO_ERROR" in str(msg).upper():
            log.warning("ASR no-valid-audio ignored: %s", msg)
            return
        log.error("ASR error: %s", msg)
        self._on_event(RecognitionEvent(
            kind=RecognitionKind.ERROR.value,
            code=ASR_PROTOCOL_ERROR,
            message=str(msg),
            retryable=True,
        ))
