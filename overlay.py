"""Overlay window for partial ASR text."""

from __future__ import annotations

try:
    from PySide6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve
    from PySide6.QtGui import QScreen
    from PySide6.QtWidgets import QApplication, QLabel, QWidget, QVBoxLayout
except Exception:  # pragma: no cover
    Qt = None  # type: ignore
    QTimer = None  # type: ignore
    QPropertyAnimation = None  # type: ignore
    QEasingCurve = None  # type: ignore
    QScreen = None  # type: ignore
    QApplication = None  # type: ignore
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
        self.setFixedWidth(600)

        self._label = QLabel("")
        self._label.setWordWrap(True)
        self._label.setStyleSheet(
            "color: white; font-size: 18px; padding: 16px;"
            "background: rgba(0,0,0,190); border-radius: 12px;"
        )

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._label)
        self.setLayout(layout)

        self._hide_timer: QTimer | None = None

    def _center_top(self) -> None:
        """Position the window at the top center of the primary screen."""
        if QApplication is None:
            return
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        geom = screen.availableGeometry()
        self.adjustSize()
        x = geom.x() + (geom.width() - self.width()) // 2
        y = geom.y() + 40  # 40px below menu bar
        self.move(x, y)

    def set_text(self, text: str) -> None:
        """Update overlay text and show at screen top center."""
        self._cancel_hide_timer()
        self._label.setText(text)
        self._center_top()
        self.show()

    def hide_with_delay(self, delay_ms: int = 400) -> None:
        """Hide the overlay window after a short delay."""
        self._cancel_hide_timer()
        if QTimer is not None:
            self._hide_timer = QTimer()
            self._hide_timer.setSingleShot(True)
            self._hide_timer.timeout.connect(self.hide)
            self._hide_timer.start(delay_ms)

    def show_error(self, text: str, hide_after_ms: int = 2000) -> None:
        """Show an error message and auto-hide after given ms."""
        self._label.setStyleSheet(
            "color: #FF6B6B; font-size: 18px; padding: 16px;"
            "background: rgba(0,0,0,210); border-radius: 12px;"
        )
        self.set_text(f"⚠️ {text}")
        self.hide_with_delay(hide_after_ms)

    def _cancel_hide_timer(self) -> None:
        if self._hide_timer is not None:
            self._hide_timer.stop()
            self._hide_timer = None

    def _reset_style(self) -> None:
        """Reset label style to default (white text)."""
        self._label.setStyleSheet(
            "color: white; font-size: 18px; padding: 16px;"
            "background: rgba(0,0,0,190); border-radius: 12px;"
        )
