"""Protocol interfaces used by SessionController."""

from __future__ import annotations

from queue import Queue
from typing import Callable, Protocol

from models import AudioFrame, PasteResult, RecognitionEvent


class Recorder(Protocol):
    def start(self, audio_queue: Queue[AudioFrame | None]) -> None: ...

    def stop(self) -> None: ...


class RecognizerAdapter(Protocol):
    def start(
        self,
        audio_queue: Queue[AudioFrame | None],
        on_event: Callable[[RecognitionEvent], None],
    ) -> None: ...

    def stop(self) -> None: ...


class PasteService(Protocol):
    def paste_text(self, text: str) -> PasteResult: ...


class ConfigStore(Protocol):
    def get_api_key(self) -> str: ...

    def set_api_key(self, key: str) -> None: ...

    def get_hotkey(self) -> str: ...

    def set_hotkey(self, hotkey: str) -> None: ...
