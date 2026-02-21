"""Auto paste service for text insertion."""

from __future__ import annotations

import time

from errors import NO_ACTIVE_TARGET
from models import PasteResult

try:
    import pyperclip
except Exception:  # pragma: no cover
    pyperclip = None  # type: ignore

try:
    from pynput.keyboard import Controller, Key
except Exception:  # pragma: no cover
    Controller = None  # type: ignore
    Key = None  # type: ignore


class ClipboardPasteService:
    def __init__(self, restore_delay_s: float = 0.1) -> None:
        self._restore_delay_s = restore_delay_s

    def paste_text(self, text: str) -> PasteResult:
        if not text.strip():
            return PasteResult(success=False, reason="empty text", clipboard_restored=True)
        if pyperclip is None or Controller is None or Key is None:
            return PasteResult(
                success=False,
                reason="clipboard/keyboard dependency missing",
                clipboard_restored=False,
            )

        old_clip: str | None = None
        restored = False
        try:
            old_clip = pyperclip.paste()
            pyperclip.copy(text)
            keyboard = Controller()
            keyboard.press(Key.cmd)
            keyboard.press("v")
            keyboard.release("v")
            keyboard.release(Key.cmd)
            time.sleep(self._restore_delay_s)
            pyperclip.copy(old_clip)
            restored = True
            return PasteResult(success=True, reason="ok", clipboard_restored=True)
        except Exception as exc:
            try:
                if old_clip is not None:
                    pyperclip.copy(old_clip)
                    restored = True
            except Exception:
                restored = False
            return PasteResult(
                success=False,
                reason=f"{NO_ACTIVE_TARGET}: {exc}",
                clipboard_restored=restored,
            )
