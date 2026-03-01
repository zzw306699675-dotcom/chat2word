from __future__ import annotations

import auto_paste
from auto_paste import ClipboardPasteService, detect_input_focus_state


class _FakeApp:
    def __init__(self, name: str, pid: int = 100) -> None:
        self._name = name
        self._pid = pid

    def localizedName(self) -> str:
        return self._name

    def processIdentifier(self) -> int:
        return self._pid


def test_paste_returns_failure_when_dependencies_missing(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(auto_paste, "pyperclip", None)
    monkeypatch.setattr(auto_paste, "_HAS_QUARTZ", False)

    service = ClipboardPasteService()
    result = service.paste_text("hello")

    assert result.success is False
    assert result.clipboard_restored is False


def test_paste_returns_failure_on_empty_text() -> None:
    service = ClipboardPasteService()
    result = service.paste_text("   ")

    assert result.success is False
    assert result.clipboard_restored is True


def test_detect_focus_returns_unknown_when_no_frontmost_app(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(auto_paste, "get_frontmost_app", lambda: None)

    state = detect_input_focus_state()

    assert state.status == "unknown"
    assert "前台应用" in state.message


def test_detect_focus_finder_is_not_editable() -> None:
    state = detect_input_focus_state(target_app=_FakeApp("Finder"))

    assert state.status == "not_editable"
    assert state.app_name == "Finder"


def test_detect_focus_returns_unknown_without_ax(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(auto_paste, "_HAS_AX", False)

    state = detect_input_focus_state(target_app=_FakeApp("TextEdit"))

    assert state.status == "unknown"
    assert state.app_name == "TextEdit"
    assert "组件" in state.message


def test_detect_focus_returns_unknown_when_ax_not_trusted(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(auto_paste, "_HAS_AX", True)
    monkeypatch.setattr(auto_paste, "_AX_TRUST_CHECK", lambda: False)

    state = detect_input_focus_state(target_app=_FakeApp("TextEdit"))

    assert state.status == "unknown"
    assert "辅助功能权限" in state.message


def test_detect_focus_editable_by_ax_role(monkeypatch) -> None:  # noqa: ANN001
    focus_element = object()
    app_ref = object()

    def _fake_get_attr(element, attr: str):  # noqa: ANN001
        if element is app_ref and attr == "AXFocusedUIElement":
            return focus_element
        if element is focus_element and attr == "AXRole":
            return "AXTextField"
        if element is focus_element and attr == "AXEditable":
            return None
        return None

    monkeypatch.setattr(auto_paste, "_HAS_AX", True)
    monkeypatch.setattr(auto_paste, "_AX_TRUST_CHECK", lambda: True)
    monkeypatch.setattr(
        auto_paste,
        "AXUIElementCreateApplication",
        lambda _pid: app_ref,
        raising=False,
    )
    monkeypatch.setattr(auto_paste, "_ax_get_attr", _fake_get_attr)

    state = detect_input_focus_state(target_app=_FakeApp("Notes", pid=321))

    assert state.status == "editable"
    assert state.app_name == "Notes"
