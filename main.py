"""Application entrypoint."""

from __future__ import annotations

import logging
import os
import sys
import threading
import traceback
from pathlib import Path

# Log to both stderr and file (so Finder-launched app has logs)
_log_file = Path.home() / "Library" / "Logs" / "ASR-Assistant.log"
_log_file.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stderr),
        logging.FileHandler(str(_log_file), encoding="utf-8"),
    ],
)
# Reduce dashscope/urllib3 noise
logging.getLogger("dashscope").setLevel(logging.INFO)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
log = logging.getLogger("app")
log.info("Log file: %s", _log_file)

from auto_paste import ClipboardPasteService
from config import JsonConfigStore
from diagnostics import export_diagnostic_snapshot
from history_logger import MarkdownHistoryLogger
from hotkey import GlobalHotkeyAdapter
from llm_adapter import QwenPolishAdapter
from overlay import OverlayWindow
from recorder import SoundDeviceRecorder
from recognizer import DashscopeRecognizerAdapter
from session_controller import SessionController
from models import SessionMode, SessionState
from subtitle_buffer import SubtitleBuffer

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
    focus_signal = Signal(str, str)  # status, message


class App:
    def __init__(self) -> None:
        self.app = QApplication(sys.argv)
        self.config_store = JsonConfigStore()
        self.overlay = OverlayWindow()
        self._subtitle_buffer = SubtitleBuffer()
        self._focus_status = "unknown"
        self._focus_message = ""
        self._ui_state = SessionState.IDLE.value
        self.ui = UIBridge()
        self.ui.partial_signal.connect(self._on_partial_ui)
        self.ui.error_signal.connect(self._on_error_ui)
        self.ui.state_signal.connect(self._on_state_change_ui)
        self.ui.focus_signal.connect(self._on_focus_hint_ui)

        api_key = self.config_store.get_api_key()
        self.controller = SessionController(
            recorder=SoundDeviceRecorder(),
            recognizer=DashscopeRecognizerAdapter(api_key=api_key),
            paste_service=ClipboardPasteService(),
            llm_adapter=QwenPolishAdapter(api_key=api_key),
            history_logger=MarkdownHistoryLogger(),
            recognizer_factory=lambda: DashscopeRecognizerAdapter(
                api_key=self.config_store.get_api_key()
            ),
            recorder_factory=lambda: SoundDeviceRecorder(),
            on_state_change=self._on_state_change,
            on_partial=self._on_partial,
            on_error=self._on_error,
            on_focus_hint=self._on_focus_hint,
        )
        self.hotkey = GlobalHotkeyAdapter(
            primary_hotkey_name=self.config_store.get_hotkey(),
            secondary_hotkey_name=self.config_store.get_secondary_hotkey(),
        )

        self.tray = QSystemTrayIcon()
        self.tray.setIcon(_create_icon(ICON_IDLE))
        self.tray.setToolTip("语音助手 - 就绪")
        self._setup_menu()
        self.tray.show()

    def _setup_menu(self) -> None:
        menu = QMenu()

        api_action = QAction("设置 API Key", menu)
        api_action.triggered.connect(self._set_api_key)
        menu.addAction(api_action)

        primary_hotkey_action = QAction("设置主快捷键", menu)
        primary_hotkey_action.triggered.connect(self._set_primary_hotkey)
        menu.addAction(primary_hotkey_action)

        llm_hotkey_action = QAction("设置润色快捷键", menu)
        llm_hotkey_action.triggered.connect(self._set_secondary_hotkey)
        menu.addAction(llm_hotkey_action)

        diagnostics_action = QAction("导出诊断信息", menu)
        diagnostics_action.triggered.connect(self._export_diagnostics)
        menu.addAction(diagnostics_action)

        menu.addSeparator()
        quit_action = QAction("退出", menu)
        quit_action.triggered.connect(self.quit)
        menu.addAction(quit_action)

        self.tray.setContextMenu(menu)

    def _set_api_key(self) -> None:
        value, ok = QInputDialog.getText(None, "API Key", "请输入 DashScope API Key")
        if not ok:
            return
        self.config_store.set_api_key(value)
        # Hot-swap recognizer with new key
        self.controller.replace_recognizer(
            DashscopeRecognizerAdapter(api_key=value)
        )
        self.controller.replace_llm_adapter(
            QwenPolishAdapter(api_key=value)
        )
        QMessageBox.information(None, "已保存", "API Key 已保存并立即生效。")

    def _set_primary_hotkey(self) -> None:
        value, ok = QInputDialog.getText(
            None, "主快捷键", "使用 pynput 按键格式，例如：Key.alt_l"
        )
        if not ok or not value:
            return
        self.config_store.set_hotkey(value)
        QMessageBox.information(None, "已保存", "主快捷键已保存，重启应用后生效。")

    def _set_secondary_hotkey(self) -> None:
        value, ok = QInputDialog.getText(
            None, "润色快捷键", "使用 pynput 按键格式，例如：Key.alt_r"
        )
        if not ok or not value:
            return
        self.config_store.set_secondary_hotkey(value)
        QMessageBox.information(None, "已保存", "润色快捷键已保存，重启应用后生效。")

    def _export_diagnostics(self) -> None:
        try:
            out = export_diagnostic_snapshot(
                controller=self.controller,
                log_file=_log_file,
                reason="manual_export",
                max_health_events=200,
            )
        except Exception as exc:
            QMessageBox.warning(None, "导出失败", f"诊断信息导出失败：\n{exc}")
            return
        QMessageBox.information(None, "导出成功", f"诊断信息已保存到：\n{out}")

    # ------------------------------------------------------------------
    # Callbacks (called from worker threads → emit signals for UI thread)
    # ------------------------------------------------------------------

    def _on_state_change(self, from_state: SessionState, to_state: SessionState) -> None:
        self.ui.state_signal.emit(from_state.value, to_state.value)

    def _on_partial(self, text: str) -> None:
        log.debug("_on_partial called: %s", text[:30] if text else "")
        self.ui.partial_signal.emit(text)

    def _on_error(self, code: str, message: str) -> None:
        self.ui.error_signal.emit(f"{code}: {message}")

    def _on_focus_hint(self, status: str, message: str) -> None:
        self.ui.focus_signal.emit(status, message)

    # ------------------------------------------------------------------
    # UI thread handlers (safe for Qt)
    # ------------------------------------------------------------------

    def _on_partial_ui(self, text: str) -> None:
        stable, live, hint = self._subtitle_buffer.on_partial(text)
        hint = self._compose_overlay_hint(hint)
        log.debug(
            "_on_partial_ui -> overlay.set_transcript stable=%d live=%d",
            len(stable),
            len(live),
        )
        self.overlay.set_transcript(stable_text=stable, live_partial=live, hint=hint)

    def _on_error_ui(self, msg: str) -> None:
        self.overlay.show_error(msg)

    def _on_state_change_ui(self, from_state: str, to_state: str) -> None:
        self._ui_state = to_state
        if to_state == SessionState.RECORDING.value:
            self.tray.setIcon(_create_icon(ICON_RECORDING))
            self.tray.setToolTip("语音助手 - 录音中...")
            self._subtitle_buffer.reset()
            self.overlay.set_transcript(
                stable_text="",
                live_partial="",
                hint=self._compose_overlay_hint("🎙️ 正在聆听..."),
            )
        elif to_state in (
            SessionState.FINALIZING.value,
            SessionState.FINALIZING_GRACE.value,
            SessionState.RECOGNIZING_DRAIN.value,
        ):
            self.tray.setToolTip("语音助手 - 处理中...")
            self.overlay.set_transcript(
                stable_text=self._subtitle_buffer.stable_text,
                live_partial=self._subtitle_buffer.live_partial,
                hint=self._compose_overlay_hint("⏳ 收尾中..."),
            )
        elif to_state == SessionState.LLM_PROCESSING.value:
            self.tray.setToolTip("语音助手 - 润色中...")
            self.overlay.set_text("✨ 润色中...")
        elif to_state == SessionState.RECOVERING.value:
            self.tray.setToolTip("语音助手 - 重连中...")
            self.overlay.set_text("🔄 重连中...")
        elif to_state == SessionState.IDLE.value:
            self.tray.setIcon(_create_icon(ICON_IDLE))
            self.tray.setToolTip("语音助手 - 就绪")
            self._subtitle_buffer.reset()
            self._focus_status = "unknown"
            self._focus_message = ""
            self.overlay.hide_with_delay(400)
        elif to_state == SessionState.ERROR.value:
            self.tray.setIcon(_create_icon(ICON_ERROR))
            self._subtitle_buffer.reset()
            self._focus_status = "unknown"
            self._focus_message = ""

    def _on_focus_hint_ui(self, status: str, message: str) -> None:
        self._focus_status = status
        self._focus_message = message
        if self._ui_state == SessionState.RECORDING.value:
            self.overlay.set_transcript(
                stable_text=self._subtitle_buffer.stable_text,
                live_partial=self._subtitle_buffer.live_partial,
                hint=self._compose_overlay_hint("🎙️ 正在聆听..."),
            )
        elif self._ui_state in (
            SessionState.FINALIZING.value,
            SessionState.FINALIZING_GRACE.value,
            SessionState.RECOGNIZING_DRAIN.value,
        ):
            self.overlay.set_transcript(
                stable_text=self._subtitle_buffer.stable_text,
                live_partial=self._subtitle_buffer.live_partial,
                hint=self._compose_overlay_hint("⏳ 收尾中..."),
            )

    def _compose_overlay_hint(self, base_hint: str) -> str:
        focus_hint = self._render_focus_hint()
        if base_hint and focus_hint:
            return f"{base_hint} {focus_hint}"
        return base_hint or focus_hint

    def _render_focus_hint(self) -> str:
        if self._focus_status == "not_editable":
            text = self._focus_message or "当前未聚焦输入框（可继续识别，结果将保留剪贴板）"
            return f"⚠️ {text}"
        return ""

    # ------------------------------------------------------------------
    # Hotkey handlers
    # ------------------------------------------------------------------

    def _on_hotkey_press(self, mode: SessionMode) -> None:
        try:
            log.info("Hotkey PRESSED — starting session mode=%s", mode.value)
            self.controller.start_session(mode=mode)
        except Exception as exc:
            log.error("start_session failed: %s", exc, exc_info=True)
            self.ui.error_signal.emit(f"启动录音失败：{exc}")

    def _on_hotkey_release(self, mode: SessionMode) -> None:
        def _do_stop() -> None:
            try:
                log.info("Hotkey RELEASED — stopping session mode=%s", mode.value)
                self.controller.stop_session()
            except Exception as exc:
                log.error("stop_session failed: %s", exc, exc_info=True)
                self.ui.error_signal.emit(f"停止录音失败：{exc}")

        # Run stop_session in a background thread to avoid blocking
        # the Qt main thread (it waits for final result with timeout)
        threading.Thread(target=_do_stop, daemon=True).start()

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
            self.overlay.show_error(f"快捷键不可用：{exc}")
        return self.app.exec()

    def quit(self) -> None:
        self.hotkey.stop()
        self.controller.cancel_session("app quit")
        self.app.quit()


def main() -> int:
    log.info("Starting ASR Assistant")
    try:
        app = App()
        return app.run()
    except Exception as exc:
        log.critical("Fatal error: %s", exc, exc_info=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
