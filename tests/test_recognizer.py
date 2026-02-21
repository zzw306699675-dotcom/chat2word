"""Tests for DashscopeRecognizerAdapter."""

from __future__ import annotations

import threading
import time
from queue import Queue
from unittest.mock import MagicMock, patch

import pytest

from models import AudioFrame, RecognitionEvent, RecognitionKind
from recognizer import DashscopeRecognizerAdapter, _pcm_to_wav_base64


# ---------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------

def _make_frame(n_samples: int = 1600) -> AudioFrame:
    """Generate a silent AudioFrame (all zeros)."""
    return AudioFrame(
        pcm16_bytes=b"\x00\x00" * n_samples,
        sample_rate=16000,
        channels=1,
        timestamp_ms=0,
    )


def _wait_for_events(events: list, *, timeout: float = 3.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if any(e.kind == RecognitionKind.FINAL.value or e.kind == RecognitionKind.ERROR.value for e in events):
            return
        time.sleep(0.05)


# ---------------------------------------------------------------
# _pcm_to_wav_base64
# ---------------------------------------------------------------

def test_pcm_to_wav_base64_produces_valid_base64() -> None:
    pcm = b"\x00\x00" * 1600  # 100ms of silence at 16kHz
    result = _pcm_to_wav_base64(pcm, sample_rate=16000, channels=1)
    assert isinstance(result, str)
    assert len(result) > 0
    # Decode should not raise
    import base64
    decoded = base64.b64decode(result)
    # WAV header starts with RIFF
    assert decoded[:4] == b"RIFF"


# ---------------------------------------------------------------
# Empty audio
# ---------------------------------------------------------------

def test_empty_audio_emits_final_with_empty_text() -> None:
    adapter = DashscopeRecognizerAdapter(api_key="test-key")
    events: list[RecognitionEvent] = []
    q: Queue[AudioFrame | None] = Queue()
    q.put(None)  # immediate Sentinel

    adapter.start(q, events.append)
    _wait_for_events(events)
    adapter.stop()

    assert len(events) == 1
    assert events[0].kind == RecognitionKind.FINAL.value
    assert events[0].text == ""


# ---------------------------------------------------------------
# Missing API key
# ---------------------------------------------------------------

@patch("recognizer.dashscope", MagicMock())
@patch.dict("os.environ", {"DASHSCOPE_API_KEY": ""}, clear=False)
def test_missing_api_key_emits_error() -> None:
    adapter = DashscopeRecognizerAdapter(api_key="")
    events: list[RecognitionEvent] = []
    q: Queue[AudioFrame | None] = Queue()
    q.put(_make_frame())
    q.put(None)

    adapter.start(q, events.append)
    _wait_for_events(events)
    adapter.stop()

    assert any(e.kind == RecognitionKind.ERROR.value for e in events)
    error = next(e for e in events if e.kind == RecognitionKind.ERROR.value)
    assert error.code == "AUTH_FAILED"


# ---------------------------------------------------------------
# Mock dashscope streaming response
# ---------------------------------------------------------------

def _fake_streaming_response():
    """Simulate dashscope streaming chunks."""
    yield {"output": {"choices": [{"message": {"content": [{"text": "你"}]}}]}}
    yield {"output": {"choices": [{"message": {"content": [{"text": "你好"}]}}]}}
    yield {"output": {"choices": [{"message": {"content": [{"text": "你好世界"}]}}]}}


@patch("recognizer.dashscope")
def test_successful_streaming_emits_partials_and_final(mock_ds: MagicMock) -> None:
    mock_ds.MultiModalConversation.call.return_value = _fake_streaming_response()

    adapter = DashscopeRecognizerAdapter(api_key="test-key")
    events: list[RecognitionEvent] = []
    q: Queue[AudioFrame | None] = Queue()
    q.put(_make_frame())
    q.put(None)

    adapter.start(q, events.append)
    _wait_for_events(events)
    adapter.stop()

    partials = [e for e in events if e.kind == RecognitionKind.PARTIAL.value]
    finals = [e for e in events if e.kind == RecognitionKind.FINAL.value]

    assert len(partials) == 3
    assert partials[0].text == "你"
    assert partials[1].text == "你好"
    assert partials[2].text == "你好世界"
    assert len(finals) == 1
    assert finals[0].text == "你好世界"


# ---------------------------------------------------------------
# Network error mapping
# ---------------------------------------------------------------

@patch("recognizer.dashscope")
def test_network_error_maps_correctly(mock_ds: MagicMock) -> None:
    mock_ds.MultiModalConversation.call.side_effect = ConnectionError("network timeout")

    adapter = DashscopeRecognizerAdapter(api_key="test-key")
    events: list[RecognitionEvent] = []
    q: Queue[AudioFrame | None] = Queue()
    q.put(_make_frame())
    q.put(None)

    adapter.start(q, events.append)
    _wait_for_events(events)
    adapter.stop()

    errors = [e for e in events if e.kind == RecognitionKind.ERROR.value]
    assert len(errors) == 1
    assert errors[0].code == "NETWORK_ERROR"
    assert errors[0].retryable is True


# ---------------------------------------------------------------
# Auth error mapping
# ---------------------------------------------------------------

@patch("recognizer.dashscope")
def test_auth_error_maps_correctly(mock_ds: MagicMock) -> None:
    mock_ds.MultiModalConversation.call.side_effect = Exception("401 Unauthorized: invalid api key")

    adapter = DashscopeRecognizerAdapter(api_key="bad-key")
    events: list[RecognitionEvent] = []
    q: Queue[AudioFrame | None] = Queue()
    q.put(_make_frame())
    q.put(None)

    adapter.start(q, events.append)
    _wait_for_events(events)
    adapter.stop()

    errors = [e for e in events if e.kind == RecognitionKind.ERROR.value]
    assert len(errors) == 1
    assert errors[0].code == "AUTH_FAILED"
    assert errors[0].retryable is False


# ---------------------------------------------------------------
# dashscope not installed
# ---------------------------------------------------------------

@patch("recognizer.dashscope", None)
def test_dashscope_not_installed_emits_error() -> None:
    adapter = DashscopeRecognizerAdapter(api_key="test-key")
    events: list[RecognitionEvent] = []
    q: Queue[AudioFrame | None] = Queue()
    q.put(_make_frame())
    q.put(None)

    adapter.start(q, events.append)
    _wait_for_events(events)
    adapter.stop()

    errors = [e for e in events if e.kind == RecognitionKind.ERROR.value]
    assert len(errors) == 1
    assert "not installed" in errors[0].message


# ---------------------------------------------------------------
# Stop event during recognition
# ---------------------------------------------------------------

@patch("recognizer.dashscope")
def test_stop_during_streaming_cancels_gracefully(mock_ds: MagicMock) -> None:
    def slow_response():
        yield {"output": {"choices": [{"message": {"content": [{"text": "hello"}]}}]}}
        time.sleep(5)  # hang to simulate slow stream
        yield {"output": {"choices": [{"message": {"content": [{"text": "world"}]}}]}}

    mock_ds.MultiModalConversation.call.return_value = slow_response()

    adapter = DashscopeRecognizerAdapter(api_key="test-key")
    events: list[RecognitionEvent] = []
    q: Queue[AudioFrame | None] = Queue()
    q.put(_make_frame())
    q.put(None)

    adapter.start(q, events.append)
    time.sleep(0.3)  # let worker pick up and start streaming
    adapter.stop()
    time.sleep(0.2)

    # Should NOT have final event since we stopped mid-stream
    finals = [e for e in events if e.kind == RecognitionKind.FINAL.value]
    assert len(finals) == 0
