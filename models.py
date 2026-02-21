"""Core data models for the app."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class SessionState(str, Enum):
    IDLE = "IDLE"
    RECORDING = "RECORDING"
    FINALIZING = "FINALIZING"
    PASTING = "PASTING"
    ERROR = "ERROR"


class RecognitionKind(str, Enum):
    PARTIAL = "partial"
    FINAL = "final"
    ERROR = "error"


@dataclass
class AudioFrame:
    pcm16_bytes: bytes
    sample_rate: int = 16000
    channels: int = 1
    timestamp_ms: int = 0


@dataclass
class RecognitionEvent:
    kind: str
    text: str = ""
    code: str = ""
    message: str = ""
    retryable: bool = False


@dataclass
class PasteResult:
    success: bool
    reason: str
    clipboard_restored: bool
