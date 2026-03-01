"""Auto paste service for text insertion.

Uses native macOS CGEvent for keyboard simulation and NSWorkspace
to restore focus to the previously active application before pasting.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from errors import NO_ACTIVE_TARGET
from models import PasteResult

log = logging.getLogger(__name__)

try:
    import pyperclip
except Exception:  # pragma: no cover
    pyperclip = None  # type: ignore

# Try native macOS keyboard simulation via Quartz
try:
    from Quartz import (
        CGEventCreateKeyboardEvent,
        CGEventPost,
        CGEventSetFlags,
        kCGHIDEventTap,
        kCGEventFlagMaskCommand,
    )
    _HAS_QUARTZ = True
except Exception:  # pragma: no cover
    _HAS_QUARTZ = False

# For saving/restoring focus
try:
    from AppKit import NSWorkspace
    _HAS_APPKIT = True
except Exception:  # pragma: no cover
    _HAS_APPKIT = False

_AX_TRUST_CHECK = None
try:
    from ApplicationServices import (  # type: ignore
        AXIsProcessTrusted,
        AXUIElementCopyAttributeValue,
        AXUIElementCreateApplication,
    )
    _AX_TRUST_CHECK = AXIsProcessTrusted
    _HAS_AX = True
except Exception:  # pragma: no cover
    try:
        from HIServices import (  # type: ignore
            AXIsProcessTrusted,
            AXUIElementCopyAttributeValue,
            AXUIElementCreateApplication,
        )
        _AX_TRUST_CHECK = AXIsProcessTrusted
        _HAS_AX = True
    except Exception:  # pragma: no cover
        _HAS_AX = False


@dataclass(frozen=True)
class FocusState:
    status: str  # editable | unknown | not_editable
    app_name: str = ""
    message: str = ""


def get_frontmost_app():
    """Get the currently focused app (to restore later)."""
    if not _HAS_APPKIT:
        return None
    try:
        return NSWorkspace.sharedWorkspace().frontmostApplication()
    except Exception:
        return None


def activate_app(app) -> None:
    """Bring a previously saved app to the front."""
    if app is None:
        return
    try:
        app.activateWithOptions_(2)  # NSApplicationActivateIgnoringOtherApps
        time.sleep(0.15)  # Give macOS time to switch
    except Exception as exc:
        log.warning("Failed to activate app: %s", exc)


def detect_input_focus_state(target_app=None) -> FocusState:
    app = target_app or get_frontmost_app()
    if app is None:
        return FocusState(status="unknown", message="无法获取前台应用")

    app_name = ""
    try:
        app_name = app.localizedName() or ""
    except Exception:
        app_name = ""

    if app_name in {"Finder", "访达"}:
        return FocusState(status="not_editable", app_name=app_name, message="当前未聚焦输入框")

    if not _HAS_AX:
        return FocusState(status="unknown", app_name=app_name, message="当前环境缺少焦点检测组件")

    if not _is_ax_trusted():
        return FocusState(
            status="unknown",
            app_name=app_name,
            message="未授予辅助功能权限（系统设置-隐私与安全性-辅助功能）",
        )

    try:
        pid = int(app.processIdentifier())
        app_ref = AXUIElementCreateApplication(pid)
        focused = _ax_get_attr(app_ref, "AXFocusedUIElement")
        if focused is None:
            return FocusState(status="unknown", app_name=app_name, message="未读取到焦点控件")
        role = str(_ax_get_attr(focused, "AXRole") or "")
        editable = _ax_get_attr(focused, "AXEditable")

        if editable in (True, 1, "1", "true", "True"):
            return FocusState(status="editable", app_name=app_name, message="输入框已就绪")

        editable_roles = {
            "AXTextField",
            "AXTextArea",
            "AXSearchField",
            "AXComboBox",
            "AXWebArea",
        }
        if role in editable_roles:
            return FocusState(status="editable", app_name=app_name, message="输入框已就绪")

        # Web and Electron editors may expose caret/selection without AXEditable.
        selected_range = _ax_get_attr(focused, "AXSelectedTextRange")
        if selected_range is not None:
            return FocusState(status="editable", app_name=app_name, message="输入框已就绪")

        if role:
            return FocusState(status="not_editable", app_name=app_name, message="当前未聚焦输入框")
        return FocusState(status="unknown", app_name=app_name, message="焦点状态未知")
    except Exception:
        return FocusState(status="unknown", app_name=app_name, message="输入焦点检测失败")


def _ax_get_attr(element: Any, attr: str) -> Any:
    result = AXUIElementCopyAttributeValue(element, attr)
    if isinstance(result, tuple) and len(result) == 2:
        err, value = result
        if err == 0:
            return value
        return None
    return result


def _is_ax_trusted() -> bool:
    if _AX_TRUST_CHECK is None:
        return True
    try:
        return bool(_AX_TRUST_CHECK())
    except Exception:
        return False


def _simulate_cmd_v() -> None:
    """Simulate Cmd+V using native macOS Quartz CGEvent."""
    if not _HAS_QUARTZ:
        raise RuntimeError("Quartz not available for keyboard simulation")

    # Key code 9 = 'v' on macOS
    v_keycode = 9

    # Key down with Cmd flag
    event_down = CGEventCreateKeyboardEvent(None, v_keycode, True)
    CGEventSetFlags(event_down, kCGEventFlagMaskCommand)
    CGEventPost(kCGHIDEventTap, event_down)

    time.sleep(0.05)

    # Key up
    event_up = CGEventCreateKeyboardEvent(None, v_keycode, False)
    CGEventSetFlags(event_up, kCGEventFlagMaskCommand)
    CGEventPost(kCGHIDEventTap, event_up)


class ClipboardPasteService:
    def __init__(self, restore_delay_s: float = 0.3) -> None:
        self._restore_delay_s = restore_delay_s

    def paste_text(self, text: str, target_app=None) -> PasteResult:
        if not text.strip():
            return PasteResult(success=False, reason="empty text", clipboard_restored=True)
        if pyperclip is None:
            return PasteResult(
                success=False,
                reason="pyperclip not installed",
                clipboard_restored=False,
            )
        if not _HAS_QUARTZ:
            return PasteResult(
                success=False,
                reason="Quartz not available for keyboard simulation",
                clipboard_restored=False,
            )

        old_clip: str | None = None
        restored = False
        try:
            old_clip = pyperclip.paste()
            pyperclip.copy(text)

            # Restore focus to the target app before pasting
            if target_app is not None:
                log.info("Restoring focus to: %s", target_app.localizedName())
                activate_app(target_app)

            log.info("Pasting text (%d chars) via Cmd+V", len(text))
            _simulate_cmd_v()
            time.sleep(self._restore_delay_s)
            pyperclip.copy(old_clip)
            restored = True
            log.info("Paste completed, clipboard restored")
            return PasteResult(success=True, reason="ok", clipboard_restored=True)
        except Exception as exc:
            log.error("Paste failed: %s", exc, exc_info=True)
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
