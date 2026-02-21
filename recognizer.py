"""ASR recognizer adapter using DashScope qwen3-asr-flash.

The qwen3-asr-flash model accepts complete audio (file path, URL, or base64)
and streams back recognition results via ``stream=True``.  We collect PCM
frames from the audio queue, convert to a temporary WAV file, and feed
it to the model.  Partial results flow through ``on_event`` in real time.
"""

from __future__ import annotations

import base64
import io
import os
import struct
import threading
import wave
from queue import Empty, Queue
from typing import Callable, Optional

from errors import ASR_PROTOCOL_ERROR, AUTH_FAILED, NETWORK_ERROR
from models import AudioFrame, RecognitionEvent, RecognitionKind

try:
    import dashscope
except Exception:  # pragma: no cover
    dashscope = None  # type: ignore


def _pcm_to_wav_base64(
    pcm: bytes,
    sample_rate: int = 16000,
    channels: int = 1,
    sample_width: int = 2,
) -> str:
    """Convert raw PCM bytes to a base64-encoded WAV string."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    wav_bytes = buf.getvalue()
    return base64.b64encode(wav_bytes).decode("ascii")


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

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _worker(self) -> None:
        """Consume audio frames until Sentinel, then recognise."""
        if self._audio_queue is None or self._on_event is None:
            return

        pcm = bytearray()
        sample_rate = 16000
        channels = 1

        while not self._stop_event.is_set():
            try:
                frame = self._audio_queue.get(timeout=0.2)
            except Empty:
                continue
            if frame is None:  # Sentinel
                break
            pcm.extend(frame.pcm16_bytes)
            sample_rate = frame.sample_rate
            channels = frame.channels

        if self._stop_event.is_set():
            return

        if not pcm:
            self._on_event(RecognitionEvent(kind=RecognitionKind.FINAL.value, text=""))
            return

        # Convert collected PCM to base64-encoded WAV for dashscope
        wav_b64 = _pcm_to_wav_base64(bytes(pcm), sample_rate, channels)
        self._recognize_stream(wav_b64)

    def _recognize_stream(self, wav_base64: str) -> None:  # noqa: C901
        """Send audio to dashscope and stream partial/final results."""
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

        api_key = self._api_key or os.getenv("DASHSCOPE_API_KEY", "")
        if not api_key:
            self._on_event(
                RecognitionEvent(
                    kind=RecognitionKind.ERROR.value,
                    code=AUTH_FAILED,
                    message="No API key configured",
                    retryable=False,
                )
            )
            return

        try:
            response = dashscope.MultiModalConversation.call(
                api_key=api_key,
                model=self._model,
                messages=[
                    {"role": "system", "content": [{"text": ""}]},
                    {"role": "user", "content": [{"audio": wav_base64}]},
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
        """Pull text from a dashscope streaming chunk dict."""
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
