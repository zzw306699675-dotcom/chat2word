from __future__ import annotations

from subtitle_buffer import SubtitleBuffer


def test_partial_growth_updates_live_without_losing_text() -> None:
    buf = SubtitleBuffer()

    stable, live, hint = buf.on_partial("今天我们")
    assert (stable, live, hint) == ("", "今天我们", "")

    stable, live, hint = buf.on_partial("今天我们讨论一下")
    assert (stable, live, hint) == ("", "今天我们讨论一下", "")


def test_partial_reset_commits_previous_live_to_stable() -> None:
    buf = SubtitleBuffer()
    buf.on_partial("第一段内容")

    stable, live, hint = buf.on_partial("第二段开头")
    assert stable == "第一段内容"
    assert live == "第二段开头"
    assert hint == ""


def test_empty_partial_keeps_existing_text_and_shows_hint() -> None:
    buf = SubtitleBuffer()
    buf.on_partial("我在思考中")

    stable, live, hint = buf.on_partial("   ")
    assert stable == ""
    assert live == "我在思考中"
    assert hint == "（思考中）"


def test_reset_clears_stable_and_live() -> None:
    buf = SubtitleBuffer()
    buf.on_partial("第一段")
    buf.on_partial("第二段")
    buf.reset()

    assert buf.stable_text == ""
    assert buf.live_partial == ""
