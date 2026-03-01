"""Global hotkey adapter using NSEvent global monitor (macOS Cocoa).

Uses NSEvent.addGlobalMonitorForEventsMatchingMask:handler: which
integrates with the application's main run loop (Qt-compatible).
Falls back to pynput if AppKit is unavailable.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

from models import SessionMode

log = logging.getLogger(__name__)

try:
    from AppKit import (
        NSEvent,
        NSAlternateKeyMask,
        NSCommandKeyMask,
        NSControlKeyMask,
        NSShiftKeyMask,
    )

    _HAS_APPKIT = True
except ImportError:
    _HAS_APPKIT = False
    NSAlternateKeyMask = 0
    NSCommandKeyMask = 0
    NSControlKeyMask = 0
    NSShiftKeyMask = 0

try:
    from pynput import keyboard as pynput_keyboard

    _HAS_PYNPUT = True
except ImportError:
    _HAS_PYNPUT = False

# macOS virtual keycodes for modifier keys
_KEYCODE_TO_NAME = {
    58: "option_l",   # Left Option/Alt
    61: "option_r",   # Right Option/Alt
    55: "cmd_l",      # Left Command
    54: "cmd_r",      # Right Command
    59: "ctrl_l",     # Left Control
    62: "ctrl_r",     # Right Control
    56: "shift_l",    # Left Shift
    60: "shift_r",    # Right Shift
}

# Map user-friendly names to our internal key identifiers
_NAME_TO_KEY = {
    "Key.alt_l": "option_l",
    "Key.alt_r": "option_r",
    "Key.cmd_l": "cmd_l",
    "Key.cmd_r": "cmd_r",
    "Key.ctrl_l": "ctrl_l",
    "Key.ctrl_r": "ctrl_r",
    "Key.shift_l": "shift_l",
    "Key.shift_r": "shift_r",
    "option_l": "option_l",
    "option_r": "option_r",
    "cmd_l": "cmd_l",
    "cmd_r": "cmd_r",
    "ctrl_l": "ctrl_l",
    "ctrl_r": "ctrl_r",
    "shift_l": "shift_l",
    "shift_r": "shift_r",
}

# Modifier flag masks
_KEY_TO_MASK = {
    "option_l": NSAlternateKeyMask,
    "option_r": NSAlternateKeyMask,
    "cmd_l": NSCommandKeyMask,
    "cmd_r": NSCommandKeyMask,
    "ctrl_l": NSControlKeyMask,
    "ctrl_r": NSControlKeyMask,
    "shift_l": NSShiftKeyMask,
    "shift_r": NSShiftKeyMask,
}


class GlobalHotkeyAdapter:
    """Cross-backend hotkey adapter. Prefers NSEvent, falls back to pynput."""

    def __init__(
        self,
        primary_hotkey_name: str = "Key.alt_l",
        secondary_hotkey_name: str = "Key.alt_r",
    ) -> None:
        self._primary_hotkey_name = primary_hotkey_name
        self._secondary_hotkey_name = secondary_hotkey_name
        self._primary_key = _NAME_TO_KEY.get(primary_hotkey_name, "option_l")
        self._secondary_key = _NAME_TO_KEY.get(secondary_hotkey_name, "option_r")

        self._key_to_mode = {self._primary_key: SessionMode.RAW}
        if self._secondary_key == self._primary_key:
            log.warning(
                "Primary and secondary hotkeys are identical (%s). "
                "Secondary LLM hotkey is disabled until configuration changes.",
                self._primary_key,
            )
        else:
            self._key_to_mode[self._secondary_key] = SessionMode.POLISH
        self._active_key: str | None = None

        self._lock = threading.Lock()
        self._monitor_global = None  # NSEvent global monitor
        self._monitor_local = None   # NSEvent local monitor
        self._on_press: Optional[Callable[[SessionMode], None]] = None
        self._on_release: Optional[Callable[[SessionMode], None]] = None

        # pynput fallback
        self._pynput_listener = None

    def start(
        self,
        on_press: Callable[[SessionMode], None],
        on_release: Callable[[SessionMode], None],
    ) -> None:
        self._on_press = on_press
        self._on_release = on_release

        if _HAS_APPKIT:
            log.info(
                "Using NSEvent monitor for hotkeys: primary=%s(%s), secondary=%s(%s)",
                self._primary_hotkey_name,
                self._primary_key,
                self._secondary_hotkey_name,
                self._secondary_key,
            )
            self._start_nsevent()
        elif _HAS_PYNPUT:
            log.info(
                "AppKit unavailable, falling back to pynput for hotkeys: %s / %s",
                self._primary_hotkey_name,
                self._secondary_hotkey_name,
            )
            self._start_pynput()
        else:
            raise RuntimeError("Neither AppKit (pyobjc) nor pynput is installed")

    def stop(self) -> None:
        if self._monitor_global is not None and _HAS_APPKIT:
            NSEvent.removeMonitor_(self._monitor_global)
            self._monitor_global = None
        if self._monitor_local is not None and _HAS_APPKIT:
            NSEvent.removeMonitor_(self._monitor_local)
            self._monitor_local = None
            log.info("NSEvent monitors removed")
        if self._pynput_listener is not None:
            self._pynput_listener.stop()
            self._pynput_listener = None
        with self._lock:
            self._active_key = None

    # ------------------------------------------------------------------
    # Shared key state handler
    # ------------------------------------------------------------------

    def _handle_key_state(self, key_name: str, is_pressed: bool) -> None:
        mode = self._key_to_mode.get(key_name)
        if mode is None:
            return

        should_emit_press = False
        should_emit_release = False

        with self._lock:
            if is_pressed:
                if self._active_key is not None:
                    return
                self._active_key = key_name
                should_emit_press = True
            else:
                if self._active_key != key_name:
                    return
                self._active_key = None
                should_emit_release = True

        if should_emit_press:
            log.info("Hotkey PRESSED: %s mode=%s", key_name, mode.value)
            if self._on_press:
                self._on_press(mode)
        elif should_emit_release:
            log.info("Hotkey RELEASED: %s mode=%s", key_name, mode.value)
            if self._on_release:
                self._on_release(mode)

    # ------------------------------------------------------------------
    # NSEvent global monitor backend
    # ------------------------------------------------------------------

    def _start_nsevent(self) -> None:
        """Register global/local monitors for NSFlagsChanged events."""
        # 12 is NSFlagsChanged event type. Mask is 1 << 12.
        mask = 1 << 12

        def handler(event):
            try:
                if event.type() != 12:  # NSFlagsChanged
                    return

                keycode = event.keyCode()
                key_name = _KEYCODE_TO_NAME.get(keycode, "")
                if key_name not in self._key_to_mode:
                    return

                flags = event.modifierFlags()
                expected_mask = _KEY_TO_MASK.get(key_name, 0)
                is_pressed = bool(flags & expected_mask)
                self._handle_key_state(key_name, is_pressed)
            except Exception as exc:
                log.error("NSEvent handler error: %s", exc, exc_info=True)

        # Global monitor: captures events from OTHER apps
        self._monitor_global = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(mask, handler)

        # Local monitor: captures events from THIS app (must return event)
        def local_handler(event):
            handler(event)
            return event

        self._monitor_local = NSEvent.addLocalMonitorForEventsMatchingMask_handler_(mask, local_handler)

        if self._monitor_global is None and self._monitor_local is None:
            log.error("Failed to create NSEvent monitors")
            if _HAS_PYNPUT:
                log.info("Falling back to pynput")
                self._start_pynput()
        else:
            log.info("NSEvent monitors active")

    # ------------------------------------------------------------------
    # pynput fallback
    # ------------------------------------------------------------------

    def _start_pynput(self) -> None:
        if not _HAS_PYNPUT:
            raise RuntimeError("pynput is not installed")

        def _on_press(key: object) -> None:
            key_name = _NAME_TO_KEY.get(str(key), "")
            if not key_name:
                return
            self._handle_key_state(key_name, True)

        def _on_release(key: object) -> None:
            key_name = _NAME_TO_KEY.get(str(key), "")
            if not key_name:
                return
            self._handle_key_state(key_name, False)

        self._pynput_listener = pynput_keyboard.Listener(
            on_press=_on_press,
            on_release=_on_release,
        )
        self._pynput_listener.start()
