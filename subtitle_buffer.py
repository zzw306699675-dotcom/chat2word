"""Subtitle display buffer for stable+live transcript rendering."""

from __future__ import annotations


class SubtitleBuffer:
    """Builds display text from a stream of partial ASR texts."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._stable_text = ""
        self._live_partial = ""

    def on_partial(self, text: str) -> tuple[str, str, str]:
        cleaned = text.strip()
        if not cleaned:
            return self._stable_text, self._live_partial, "（思考中）"

        if not self._live_partial:
            self._live_partial = cleaned
            return self._stable_text, self._live_partial, ""

        if cleaned.startswith(self._live_partial):
            # Normal streaming growth.
            self._live_partial = cleaned
            return self._stable_text, self._live_partial, ""

        # Partial stream rewound or restarted; preserve previous content.
        self._commit_live_partial()
        self._live_partial = cleaned
        return self._stable_text, self._live_partial, ""

    @property
    def stable_text(self) -> str:
        return self._stable_text

    @property
    def live_partial(self) -> str:
        return self._live_partial

    def _commit_live_partial(self) -> None:
        live = self._live_partial.strip()
        if not live:
            self._live_partial = ""
            return
        if not self._stable_text:
            self._stable_text = live
        elif not self._stable_text.endswith(live):
            self._stable_text = f"{self._stable_text} {live}".strip()
        self._live_partial = ""
