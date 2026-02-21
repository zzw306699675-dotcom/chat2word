from __future__ import annotations

from pathlib import Path

from config import JsonConfigStore


def test_config_read_write(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    store = JsonConfigStore(path=path)

    assert store.get_api_key() == ""
    assert store.get_hotkey() == "Key.alt_l"

    store.set_api_key("abc")
    store.set_hotkey("Key.alt_r")

    reloaded = JsonConfigStore(path=path)
    assert reloaded.get_api_key() == "abc"
    assert reloaded.get_hotkey() == "Key.alt_r"


def test_config_invalid_json_fallback(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text("{invalid", encoding="utf-8")

    store = JsonConfigStore(path=path)
    assert store.get_api_key() == ""
    assert store.get_hotkey() == "Key.alt_l"
