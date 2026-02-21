"""Global hotkey adapter based on pynput."""

from __future__ import annotations

import threading
from typing import Callable, Optional

try:
    from pynput import keyboard
except Exception:  # pragma: no cover
    keyboard = None  # type: ignore


class GlobalHotkeyAdapter:
    def __init__(self, hotkey_name: str = "Key.alt_l") -> None:
        self._hotkey_name = hotkey_name
        self._listener: Optional[object] = None
        self._pressed = False
        self._lock = threading.Lock()

    def start(self, on_press: Callable[[], None], on_release: Callable[[], None]) -> None:
        if keyboard is None:
            raise RuntimeError("pynput is not installed")

        def _on_press(key: object) -> None:
            if str(key) != self._hotkey_name:
                return
            with self._lock:
                if self._pressed:
                    return
                self._pressed = True
            on_press()

        def _on_release(key: object) -> None:
            if str(key) != self._hotkey_name:
                return
            with self._lock:
                if not self._pressed:
                    return
                self._pressed = False
            on_release()

        self._listener = keyboard.Listener(on_press=_on_press, on_release=_on_release)
        self._listener.start()

    def stop(self) -> None:
        listener = self._listener
        if listener is not None:
            listener.stop()
            self._listener = None
