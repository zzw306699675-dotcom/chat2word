"""Overlay window for partial ASR text."""

from __future__ import annotations

try:
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtWidgets import QLabel, QWidget, QVBoxLayout
except Exception:  # pragma: no cover
    Qt = None  # type: ignore
    QTimer = None  # type: ignore
    QLabel = object  # type: ignore
    QWidget = object  # type: ignore
    QVBoxLayout = object  # type: ignore


class OverlayWindow(QWidget):
    def __init__(self) -> None:
        if Qt is None:
            raise RuntimeError("PySide6 is not installed")
        super().__init__()
        self.setWindowFlags(
            Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        self._label = QLabel("")
        self._label.setStyleSheet(
            "color: white; font-size: 18px; padding: 16px;"
            "background: rgba(0,0,0,190); border-radius: 12px;"
        )

        layout = QVBoxLayout()
        layout.addWidget(self._label)
        self.setLayout(layout)

    def set_text(self, text: str) -> None:
        self._label.setText(text)
        self.show()

    def show_error(self, text: str, hide_after_ms: int = 2000) -> None:
        self.set_text(text)
        if QTimer is not None:
            QTimer.singleShot(hide_after_ms, self.hide)
