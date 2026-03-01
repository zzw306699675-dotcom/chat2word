from __future__ import annotations

from transcript_aggregator import TranscriptAggregator


def test_best_text_prefers_final_segments() -> None:
    agg = TranscriptAggregator()
    agg.on_partial("partial text")
    agg.on_final("final1")
    agg.on_final("final2")

    assert agg.final_text() == "final1final2"
    assert agg.best_text() == "final1final2"


def test_best_text_falls_back_to_partial_when_no_final() -> None:
    agg = TranscriptAggregator()
    agg.on_partial("  latest partial  ")

    assert agg.final_text() == ""
    assert agg.fallback_text() == "latest partial"
    assert agg.best_text() == "latest partial"


def test_on_final_deduplicates_adjacent_same_segment() -> None:
    agg = TranscriptAggregator()
    agg.on_final("hello")
    agg.on_final("hello")
    agg.on_final("world")

    assert agg.final_text() == "helloworld"


def test_on_final_handles_cumulative_final_update() -> None:
    agg = TranscriptAggregator()
    agg.on_final("今天")
    agg.on_final("今天天气不错")

    assert agg.final_text() == "今天天气不错"


def test_on_final_merges_overlap_suffix_prefix() -> None:
    agg = TranscriptAggregator()
    agg.on_final("今天")
    agg.on_final("天很好")

    assert agg.final_text() == "今天很好"
