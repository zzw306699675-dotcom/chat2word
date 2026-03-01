from __future__ import annotations

import threading
from datetime import datetime

from history_logger import MarkdownHistoryLogger
from models import PasteResult, SessionMode


def test_append_record_creates_daily_markdown_file(tmp_path) -> None:  # noqa: ANN001
    logger = MarkdownHistoryLogger(base_dir=tmp_path)
    ts = datetime(2026, 2, 22, 14, 30, 15)

    logger.append_record(
        mode=SessionMode.POLISH,
        raw_text="这是原文",
        polished_text="1. 这是润色结果",
        llm_error=None,
        paste_result=PasteResult(success=True, reason="ok", clipboard_restored=True),
        event_time=ts,
    )

    file_path = tmp_path / "chat2word_2026-02-22.md"
    content = file_path.read_text(encoding="utf-8")

    assert "### 14:30:15" in content
    assert "模式: POLISH" in content
    assert "粘贴: success" in content
    assert "#### 原始识别" in content
    assert "这是原文" in content
    assert "#### LLM润色" in content
    assert "1. 这是润色结果" in content


def test_append_record_uses_failure_text_when_llm_failed(tmp_path) -> None:  # noqa: ANN001
    logger = MarkdownHistoryLogger(base_dir=tmp_path)
    ts = datetime(2026, 2, 22, 15, 0, 0)

    logger.append_record(
        mode=SessionMode.POLISH,
        raw_text="原文",
        polished_text=None,
        llm_error="timeout",
        paste_result=PasteResult(success=False, reason="no target", clipboard_restored=True),
        event_time=ts,
    )

    content = (tmp_path / "chat2word_2026-02-22.md").read_text(encoding="utf-8")
    assert "粘贴: failed (no target)" in content
    assert "LLM失败: timeout" in content


def test_append_record_is_thread_safe(tmp_path) -> None:  # noqa: ANN001
    logger = MarkdownHistoryLogger(base_dir=tmp_path)
    ts = datetime(2026, 2, 22, 16, 0, 0)

    def _worker(i: int) -> None:
        logger.append_record(
            mode=SessionMode.RAW,
            raw_text=f"raw-{i}",
            polished_text=None,
            llm_error=None,
            paste_result=PasteResult(success=True, reason="ok", clipboard_restored=True),
            event_time=ts,
        )

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    content = (tmp_path / "chat2word_2026-02-22.md").read_text(encoding="utf-8")
    assert content.count("### 16:00:00") == 20
    assert "raw-0" in content
    assert "raw-19" in content
