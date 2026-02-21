"""Application entrypoint."""

from __future__ import annotations

import sys

from auto_paste import ClipboardPasteService
from config import JsonConfigStore
from hotkey import GlobalHotkeyAdapter
from overlay import OverlayWindow
from recorder import SoundDeviceRecorder
from recognizer import DashscopeRecognizerAdapter
from session_controller import SessionController

try:
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import QApplication, QInputDialog, QMenu, QMessageBox, QSystemTrayIcon
except Exception as exc:  # pragma: no cover
    raise SystemExit(f"PySide6 is required to run the desktop app: {exc}")


class UIBridge(QObject):
    partial_signal = Signal(str)
    error_signal = Signal(str)


class App:
    def __init__(self) -> None:
        self.app = QApplication(sys.argv)
        self.config_store = JsonConfigStore()
        self.overlay = OverlayWindow()
        self.ui = UIBridge()
        self.ui.partial_signal.connect(self.overlay.set_text)
        self.ui.error_signal.connect(self.overlay.show_error)

        api_key = self.config_store.get_api_key()
        self.controller = SessionController(
            recorder=SoundDeviceRecorder(),
            recognizer=DashscopeRecognizerAdapter(api_key=api_key),
            paste_service=ClipboardPasteService(),
            on_partial=self._on_partial,
            on_error=self._on_error,
        )
        self.hotkey = GlobalHotkeyAdapter(hotkey_name=self.config_store.get_hotkey())

        self.tray = QSystemTrayIcon()
        self.tray.setToolTip("ASR Assistant")
        self._setup_menu()
        self.tray.show()

    def _setup_menu(self) -> None:
        menu = QMenu()

        start_action = QAction("Start", menu)
        start_action.triggered.connect(self.controller.start_session)
        menu.addAction(start_action)

        stop_action = QAction("Stop", menu)
        stop_action.triggered.connect(self.controller.stop_session)
        menu.addAction(stop_action)

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
        QMessageBox.information(None, "Saved", "API Key saved. Restart app to fully apply.")

    def _set_hotkey(self) -> None:
        value, ok = QInputDialog.getText(
            None, "Hotkey", "Use pynput key format, e.g. Key.alt_l"
        )
        if not ok or not value:
            return
        self.config_store.set_hotkey(value)
        QMessageBox.information(None, "Saved", "Hotkey saved. Restart app to apply.")

    def _on_partial(self, text: str) -> None:
        self.ui.partial_signal.emit(text)

    def _on_error(self, code: str, message: str) -> None:
        self.ui.error_signal.emit(f"{code}: {message}")

    def run(self) -> int:
        try:
            self.hotkey.start(
                on_press=self.controller.start_session,
                on_release=self.controller.stop_session,
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
