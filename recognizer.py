"""ASR recognizer adapter."""

from __future__ import annotations

import os
import threading
from queue import Empty, Queue
from typing import Callable, Optional

from errors import ASR_PROTOCOL_ERROR, AUTH_FAILED, NETWORK_ERROR
from models import AudioFrame, RecognitionEvent, RecognitionKind

try:
    import dashscope
except Exception:  # pragma: no cover
    dashscope = None  # type: ignore


class DashscopeRecognizerAdapter:
    def __init__(
        self,
        api_key: str,
        model: str = "qwen3-asr-flash",
        request_timeout_s: float = 10.0,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._request_timeout_s = request_timeout_s
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._audio_queue: Optional[Queue[AudioFrame | None]] = None
        self._on_event: Optional[Callable[[RecognitionEvent], None]] = None

    def start(
        self,
        audio_queue: Queue[AudioFrame | None],
        on_event: Callable[[RecognitionEvent], None],
    ) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._audio_queue = audio_queue
        self._on_event = on_event
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=0.5)

    def _worker(self) -> None:
        if self._audio_queue is None or self._on_event is None:
            return

        pcm = bytearray()
        while not self._stop_event.is_set():
            try:
                frame = self._audio_queue.get(timeout=0.2)
            except Empty:
                continue
            if frame is None:
                break
            pcm.extend(frame.pcm16_bytes)

        if self._stop_event.is_set():
            return

        if not pcm:
            self._on_event(RecognitionEvent(kind=RecognitionKind.FINAL.value, text=""))
            return

        self._recognize_stream(bytes(pcm))

    def _recognize_stream(self, audio_data: bytes) -> None:
        if self._on_event is None:
            return
        if dashscope is None:
            self._on_event(
                RecognitionEvent(
                    kind=RecognitionKind.ERROR.value,
                    code=ASR_PROTOCOL_ERROR,
                    message="dashscope is not installed",
                    retryable=False,
                )
            )
            return

        try:
            response = dashscope.MultiModalConversation.call(
                api_key=self._api_key or os.getenv("DASHSCOPE_API_KEY", ""),
                model=self._model,
                messages=[
                    {"role": "system", "content": [{"text": ""}]},
                    {"role": "user", "content": [{"audio": audio_data}]},
                ],
                result_format="message",
                asr_options={"enable_itn": False},
                stream=True,
                timeout=self._request_timeout_s,
            )
        except Exception as exc:
            self._on_event(self._to_error_event(exc))
            return

        latest_text = ""
        try:
            for chunk in response:
                if self._stop_event.is_set():
                    return
                text = self._extract_text(chunk)
                if text:
                    latest_text = text
                    self._on_event(
                        RecognitionEvent(kind=RecognitionKind.PARTIAL.value, text=text)
                    )
        except Exception as exc:
            self._on_event(self._to_error_event(exc))
            return

        self._on_event(
            RecognitionEvent(kind=RecognitionKind.FINAL.value, text=latest_text)
        )

    def _extract_text(self, chunk: object) -> str:
        if isinstance(chunk, dict):
            output = chunk.get("output", {})
            choices = output.get("choices", [])
            if not choices:
                return ""
            message = choices[0].get("message", {})
            content = message.get("content", [])
            if not content:
                return ""
            value = content[0]
            if isinstance(value, dict):
                return str(value.get("text", ""))
        return ""

    def _to_error_event(self, exc: Exception) -> RecognitionEvent:
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
