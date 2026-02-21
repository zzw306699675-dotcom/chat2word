"""Microphone recorder adapter."""

from __future__ import annotations

import threading
import time
from queue import Full, Queue
from typing import Any

from models import AudioFrame

try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None  # type: ignore

try:
    import sounddevice as sd
except Exception:  # pragma: no cover
    sd = None  # type: ignore


class SoundDeviceRecorder:
    def __init__(
        self,
        sample_rate: int = 16000,
        channels: int = 1,
        chunk_ms: int = 100,
    ) -> None:
        self.sample_rate = sample_rate
        self.channels = channels
        self.chunk_ms = chunk_ms
        self._stream: Any = None
        self._running = False
        self._lock = threading.Lock()
        self.dropped_chunks = 0
        self._audio_queue: Queue[AudioFrame | None] | None = None

    def start(self, audio_queue: Queue[AudioFrame | None]) -> None:
        with self._lock:
            if self._running:
                return
            if sd is None:
                raise RuntimeError("sounddevice is not installed")
            self._audio_queue = audio_queue
            blocksize = int(self.sample_rate * (self.chunk_ms / 1000.0))
            self._stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype="int16",
                blocksize=blocksize,
                callback=self._on_audio,
            )
            self._stream.start()
            self._running = True

    def stop(self) -> None:
        with self._lock:
            if not self._running:
                self._emit_sentinel_if_needed()
                return
            self._running = False
            if self._stream is not None:
                self._stream.stop()
                self._stream.close()
                self._stream = None
            self._emit_sentinel_if_needed()

    def _on_audio(self, indata: Any, frames: int, time_info: Any, status: Any) -> None:
        if not self._running or self._audio_queue is None:
            return
        if np is None:
            return
        payload = np.asarray(indata, dtype=np.int16).tobytes()
        frame = AudioFrame(
            pcm16_bytes=payload,
            sample_rate=self.sample_rate,
            channels=self.channels,
            timestamp_ms=int(time.time() * 1000),
        )
        try:
            self._audio_queue.put_nowait(frame)
        except Full:
            self.dropped_chunks += 1

    def _emit_sentinel_if_needed(self) -> None:
        if self._audio_queue is None:
            return
        try:
            self._audio_queue.put_nowait(None)
        except Full:
            pass
