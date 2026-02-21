"""Simple JSON-based config store."""

from __future__ import annotations

import json
from pathlib import Path


class JsonConfigStore:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path or Path.home() / ".config" / "qwen_asr" / "config.json"
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def get_api_key(self) -> str:
        data = self._read_all()
        return str(data.get("api_key", ""))

    def set_api_key(self, key: str) -> None:
        data = self._read_all()
        data["api_key"] = key
        self._write_all(data)

    def get_hotkey(self) -> str:
        data = self._read_all()
        return str(data.get("hotkey", "Key.alt_l"))

    def set_hotkey(self, hotkey: str) -> None:
        data = self._read_all()
        data["hotkey"] = hotkey
        self._write_all(data)

    def _read_all(self) -> dict:
        if not self._path.exists():
            return {}
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def _write_all(self, data: dict) -> None:
        self._path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
