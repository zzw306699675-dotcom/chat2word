"""Shared error codes and user-facing messages."""

from __future__ import annotations

PERMISSION_DENIED = "PERMISSION_DENIED"
NETWORK_ERROR = "NETWORK_ERROR"
AUTH_FAILED = "AUTH_FAILED"
NO_ACTIVE_TARGET = "NO_ACTIVE_TARGET"
ASR_PROTOCOL_ERROR = "ASR_PROTOCOL_ERROR"

ERROR_MESSAGES = {
    PERMISSION_DENIED: "Permission is required in macOS settings.",
    NETWORK_ERROR: "Network failed, please retry.",
    AUTH_FAILED: "API key is invalid.",
    NO_ACTIVE_TARGET: "No active input target, result kept in clipboard.",
    ASR_PROTOCOL_ERROR: "ASR response format is invalid.",
}
