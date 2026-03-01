"""
py2app setup for ASR Assistant.

Usage:
    # Development mode (symlink, fast):
    python setup.py py2app -A

    # Production mode (standalone .app):
    python setup.py py2app
"""

from setuptools import setup

APP = ["main.py"]
APP_NAME = "ASR Assistant"

DATA_FILES = [".env"]  # Bundle .env into app Resources

OPTIONS = {
    "argv_emulation": False,
    "iconfile": "chat2word_macos_icon.icns",
    "plist": {
        "CFBundleName": APP_NAME,
        "CFBundleDisplayName": APP_NAME,
        "CFBundleIdentifier": "com.zzw.asr-assistant",
        "CFBundleVersion": "0.4.0",
        "CFBundleShortVersionString": "0.4.0",
        "LSUIElement": True,  # Hide from Dock (status-bar-only app)
        "NSMicrophoneUsageDescription": "ASR Assistant needs microphone access to record your voice for speech-to-text.",
        "NSAppleEventsUsageDescription": "ASR Assistant needs accessibility access to simulate keyboard input for pasting text.",
    },
    "packages": [
        "PySide6",
        "pynput",
        "dashscope",
        "numpy",
        "pyperclip",
        "sounddevice",
        "_sounddevice_data",
        "certifi",
        "requests",
        "websocket",
        "aiohttp",
        "cffi",
        "openai",
    ],
    "includes": [
        "models",
        "errors",
        "interfaces",
        "config",
        "recorder",
        "recognizer",
        "auto_paste",
        "hotkey",
        "overlay",
        "subtitle_buffer",
        "session_controller",
        "transcript_aggregator",
        "diagnostics",
        "llm_adapter",
        "history_logger",
    ],
    "excludes": [
        "pytest",
        "pytest_qt",
        "pytest_cov",
        "coverage",
        "test",
        "tests",
        "tkinter",
        "_tkinter",
    ],
}

setup(
    app=APP,
    name=APP_NAME,
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
