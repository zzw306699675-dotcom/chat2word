"""Application entrypoint."""

from __future__ import annotations

import sys
import threading

from auto_paste import ClipboardPasteService
from config import JsonConfigStore
from hotkey import GlobalHotkeyAdapter
from overlay import OverlayWindow
from recorder import SoundDeviceRecorder
from recognizer import DashscopeRecognizerAdapter
from session_controller import SessionController
from models import SessionState

try:
    from PySide6.QtCore import QObject, Signal, QSize
    from PySide6.QtGui import QAction, QIcon, QPixmap, QPainter, QColor, QBrush
    from PySide6.QtWidgets import QApplication, QInputDialog, QMenu, QMessageBox, QSystemTrayIcon
except Exception as exc:  # pragma: no cover
    raise SystemExit(f"PySide6 is required to run the desktop app: {exc}")


def _create_icon(color: str = "#888888", size: int = 22) -> QIcon:
    """Generate a simple circular tray icon with the given color."""
    pixmap = QPixmap(QSize(size, size))
    pixmap.fill(QColor(0, 0, 0, 0))  # transparent background
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setBrush(QBrush(QColor(color)))
    painter.setPen(QColor(color))
    painter.drawEllipse(2, 2, size - 4, size - 4)
    painter.end()
    return QIcon(pixmap)


ICON_IDLE = "#888888"      # grey
ICON_RECORDING = "#FF4444"  # red
ICON_ERROR = "#FF8800"     # orange


class UIBridge(QObject):
    partial_signal = Signal(str)
    error_signal = Signal(str)
    state_signal = Signal(str, str)  # from_state, to_state


class App:
    def __init__(self) -> None:
        self.app = QApplication(sys.argv)
        self.config_store = JsonConfigStore()
        self.overlay = OverlayWindow()
        self.ui = UIBridge()
        self.ui.partial_signal.connect(self._on_partial_ui)
        self.ui.error_signal.connect(self._on_error_ui)
        self.ui.state_signal.connect(self._on_state_change_ui)

        api_key = self.config_store.get_api_key()
        self.controller = SessionController(
            recorder=SoundDeviceRecorder(),
            recognizer=DashscopeRecognizerAdapter(api_key=api_key),
            paste_service=ClipboardPasteService(),
            on_state_change=self._on_state_change,
            on_partial=self._on_partial,
            on_error=self._on_error,
        )
        self.hotkey = GlobalHotkeyAdapter(hotkey_name=self.config_store.get_hotkey())

        self.tray = QSystemTrayIcon()
        self.tray.setIcon(_create_icon(ICON_IDLE))
        self.tray.setToolTip("ASR Assistant — Ready")
        self._setup_menu()
        self.tray.show()

    def _setup_menu(self) -> None:
        menu = QMenu()

        api_action = QAction("Set API Key", menu)
        api_action.triggered.connect(self._set_api_key)
        menu.addAction(api_action)

        hotkey_action = QAction("Set Hotkey", menu)
        hotkey_action.triggered.connect(self._set_hotkey)
        menu.addAction(hotkey_action)

        menu.addSeparator()
        quit_action = QAction("Quit", menu)
        quit_action.triggered.connect(self.quit)
        menu.addAction(quit_action)

        self.tray.setContextMenu(menu)

    def _set_api_key(self) -> None:
        value, ok = QInputDialog.getText(None, "API Key", "DashScope API Key")
        if not ok:
            return
        self.config_store.set_api_key(value)
        # Hot-swap recognizer with new key
        self.controller.replace_recognizer(
            DashscopeRecognizerAdapter(api_key=value)
        )
        QMessageBox.information(None, "Saved", "API Key saved and applied.")

    def _set_hotkey(self) -> None:
        value, ok = QInputDialog.getText(
            None, "Hotkey", "Use pynput key format, e.g. Key.alt_l"
        )
        if not ok or not value:
            return
        self.config_store.set_hotkey(value)
        QMessageBox.information(None, "Saved", "Hotkey saved. Restart app to apply.")

    # ------------------------------------------------------------------
    # Callbacks (called from worker threads → emit signals for UI thread)
    # ------------------------------------------------------------------

    def _on_state_change(self, from_state: SessionState, to_state: SessionState) -> None:
        self.ui.state_signal.emit(from_state.value, to_state.value)

    def _on_partial(self, text: str) -> None:
        self.ui.partial_signal.emit(text)

    def _on_error(self, code: str, message: str) -> None:
        self.ui.error_signal.emit(f"{code}: {message}")

    # ------------------------------------------------------------------
    # UI thread handlers (safe for Qt)
    # ------------------------------------------------------------------

    def _on_partial_ui(self, text: str) -> None:
        self.overlay.set_text(text)

    def _on_error_ui(self, msg: str) -> None:
        self.overlay.show_error(msg)

    def _on_state_change_ui(self, from_state: str, to_state: str) -> None:
        if to_state == SessionState.RECORDING.value:
            self.tray.setIcon(_create_icon(ICON_RECORDING))
            self.tray.setToolTip("ASR Assistant — Recording...")
            self.overlay.set_text("🎙️ Listening...")
        elif to_state == SessionState.FINALIZING.value:
            self.tray.setToolTip("ASR Assistant — Processing...")
        elif to_state == SessionState.IDLE.value:
            self.tray.setIcon(_create_icon(ICON_IDLE))
            self.tray.setToolTip("ASR Assistant — Ready")
            self.overlay.hide_with_delay(400)
        elif to_state == SessionState.ERROR.value:
            self.tray.setIcon(_create_icon(ICON_ERROR))

    # ------------------------------------------------------------------
    # Hotkey handlers
    # ------------------------------------------------------------------

    def _on_hotkey_press(self) -> None:
        self.controller.start_session()

    def _on_hotkey_release(self) -> None:
        # Run stop_session in a background thread to avoid blocking
        # the Qt main thread (it waits for final result with timeout)
        threading.Thread(
            target=self.controller.stop_session,
            daemon=True,
        ).start()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def run(self) -> int:
        try:
            self.hotkey.start(
                on_press=self._on_hotkey_press,
                on_release=self._on_hotkey_release,
            )
        except Exception as exc:
            self.overlay.show_error(f"Hotkey disabled: {exc}")
        return self.app.exec()

    def quit(self) -> None:
        self.hotkey.stop()
        self.controller.cancel_session("app quit")
        self.app.quit()


def main() -> int:
    app = App()
    return app.run()


if __name__ == "__main__":
    raise SystemExit(main())
