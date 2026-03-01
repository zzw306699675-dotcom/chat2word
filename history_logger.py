"""Markdown history logger for ASR sessions."""

from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

from models import PasteResult, SessionMode


class MarkdownHistoryLogger:
    def __init__(self, base_dir: Path | None = None) -> None:
        self._base_dir = base_dir or (
            Path.home() / "Library" / "Application Support" / "ASR-Assistant" / "history"
        )
        self._base_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def append_record(
        self,
        *,
        mode: SessionMode,
        raw_text: str,
        polished_text: str | None,
        llm_error: str | None,
        paste_result: PasteResult,
        event_time: datetime | None = None,
    ) -> None:
        ts = event_time or datetime.now()
        file_path = self._base_dir / f"chat2word_{ts.strftime('%Y-%m-%d')}.md"

        lines = [
            f"### {ts.strftime('%H:%M:%S')}",
            f"模式: {mode.value}",
        ]
        if paste_result.success:
            lines.append("粘贴: success")
        else:
            lines.append(f"粘贴: failed ({paste_result.reason})")

        lines.extend([
            "#### 原始识别",
            raw_text.strip(),
        ])

        if mode == SessionMode.POLISH:
            lines.append("#### LLM润色")
            if polished_text and polished_text.strip():
                lines.append(polished_text.strip())
            else:
                lines.append(f"LLM失败: {llm_error or 'unknown error'}")

        lines.append("")
        payload = "\n".join(lines) + "\n"

        with self._lock:
            with file_path.open("a", encoding="utf-8") as f:
                f.write(payload)
