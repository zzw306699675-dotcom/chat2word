"""Simple JSON-based config store with .env fallback."""

from __future__ import annotations

import json
import os
from pathlib import Path


def _load_dotenv(dotenv_path: Path | None = None) -> dict[str, str]:
    """Read a .env file and return key-value pairs."""
    result: dict[str, str] = {}
    candidates = [
        dotenv_path,
        Path.cwd() / ".env",
        Path(__file__).parent / ".env",
    ]
    for p in candidates:
        if p and p.is_file():
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                result[key.strip()] = value.strip().strip('"').strip("'")
            break
    return result


class JsonConfigStore:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path or Path.home() / ".config" / "qwen_asr" / "config.json"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._dotenv = _load_dotenv()

    def get_api_key(self) -> str:
        # Priority: config.json > env var > .env file
        data = self._read_all()
        key = str(data.get("api_key", ""))
        if key:
            return key
        key = os.environ.get("DASHSCOPE_API_KEY", "")
        if key:
            return key
        return self._dotenv.get("DASHSCOPE_API_KEY", "")

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

    def get_secondary_hotkey(self) -> str:
        data = self._read_all()
        return str(data.get("secondary_hotkey", "Key.alt_r"))

    def set_secondary_hotkey(self, hotkey: str) -> None:
        data = self._read_all()
        data["secondary_hotkey"] = hotkey
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
