from __future__ import annotations

import auto_paste
from auto_paste import ClipboardPasteService


def test_paste_returns_failure_when_dependencies_missing(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(auto_paste, "pyperclip", None)
    monkeypatch.setattr(auto_paste, "Controller", None)
    monkeypatch.setattr(auto_paste, "Key", None)

    service = ClipboardPasteService()
    result = service.paste_text("hello")

    assert result.success is False
    assert result.clipboard_restored is False


def test_paste_returns_failure_on_empty_text() -> None:
    service = ClipboardPasteService()
    result = service.paste_text("   ")

    assert result.success is False
    assert result.clipboard_restored is True
