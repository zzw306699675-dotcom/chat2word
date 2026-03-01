"""Session-level transcript aggregation helpers."""

from __future__ import annotations


class TranscriptAggregator:
    """Collects partial/final recognition texts for a single session."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._final_text = ""
        self._latest_partial = ""

    def on_partial(self, text: str) -> None:
        cleaned = text.strip()
        if cleaned:
            self._latest_partial = cleaned

    def on_final(self, text: str) -> None:
        cleaned = text.strip()
        if not cleaned:
            return
        if not self._final_text:
            self._final_text = cleaned
            self._latest_partial = ""
            return

        # Ignore exact duplicate or regressed FINAL.
        if cleaned == self._final_text or self._final_text.startswith(cleaned):
            self._latest_partial = ""
            return

        # Some providers send cumulative FINAL; replace with longer cumulative text.
        if cleaned.startswith(self._final_text):
            self._final_text = cleaned
            self._latest_partial = ""
            return

        # Merge with overlap to avoid duplicated boundary text.
        overlap = _longest_suffix_prefix_overlap(self._final_text, cleaned)
        if overlap > 0:
            self._final_text = f"{self._final_text}{cleaned[overlap:]}"
        else:
            self._final_text = f"{self._final_text}{cleaned}"
        self._latest_partial = ""

    def final_text(self) -> str:
        return self._final_text

    def fallback_text(self) -> str:
        return self._latest_partial

    def best_text(self) -> str:
        final = self.final_text().strip()
        if final:
            return final
        return self.fallback_text().strip()


def _longest_suffix_prefix_overlap(left: str, right: str) -> int:
    max_overlap = min(len(left), len(right))
    for size in range(max_overlap, 0, -1):
        if left.endswith(right[:size]):
            return size
    return 0
