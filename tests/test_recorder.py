"""Tests for SoundDeviceRecorder."""

from __future__ import annotations

import time
from queue import Queue
from unittest.mock import MagicMock, patch

import pytest

from models import AudioFrame
from recorder import SoundDeviceRecorder


# ---------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------

class _FakeNp:
    """Minimal numpy stand-in so recorder._on_audio doesn't bail."""

    class int16:
        pass

    @staticmethod
    def asarray(data, dtype=None):
        """Return an object with .tobytes() that gives raw PCM."""
        if hasattr(data, 'tobytes'):
            return data
        return _BytesLike(data)


class _BytesLike:
    def __init__(self, data) -> None:
        self._data = data

    def tobytes(self) -> bytes:
        if isinstance(self._data, (bytes, bytearray)):
            return bytes(self._data)
        return bytes(self._data)


class _FakeAudioInput:
    """Fake audio input similar to what sounddevice callback provides."""

    def __init__(self, n_samples: int = 1600) -> None:
        self._data = b"\x00\x00" * n_samples

    def tobytes(self) -> bytes:
        return self._data


def _make_fake_audio_data(n_samples: int = 1600):
    return _FakeAudioInput(n_samples)


# ---------------------------------------------------------------
# Basic start / stop
# ---------------------------------------------------------------

@patch("recorder.sd")
def test_start_creates_stream_and_runs(mock_sd: MagicMock) -> None:
    mock_stream = MagicMock()
    mock_sd.InputStream.return_value = mock_stream

    recorder = SoundDeviceRecorder()
    q: Queue[AudioFrame | None] = Queue()
    recorder.start(q)

    mock_sd.InputStream.assert_called_once()
    mock_stream.start.assert_called_once()

    recorder.stop()
    mock_stream.stop.assert_called_once()
    mock_stream.close.assert_called_once()

    # Should have emitted sentinel
    assert q.get_nowait() is None


@patch("recorder.sd")
def test_start_is_idempotent(mock_sd: MagicMock) -> None:
    mock_sd.InputStream.return_value = MagicMock()

    recorder = SoundDeviceRecorder()
    q: Queue[AudioFrame | None] = Queue()
    recorder.start(q)
    recorder.start(q)  # second call should be no-op

    assert mock_sd.InputStream.call_count == 1
    recorder.stop()


@patch("recorder.sd")
def test_stop_is_idempotent(mock_sd: MagicMock) -> None:
    mock_sd.InputStream.return_value = MagicMock()

    recorder = SoundDeviceRecorder()
    q: Queue[AudioFrame | None] = Queue()
    recorder.start(q)
    recorder.stop()
    recorder.stop()  # second stop: should not raise

    # First stop emits sentinel, second stop also tries but we accept that
    sentinel_1 = q.get_nowait()
    assert sentinel_1 is None
    # The implementation emits sentinel on each stop() call which is acceptable


# ---------------------------------------------------------------
# Audio callback pushes frames to queue
# ---------------------------------------------------------------

@patch("recorder.np", _FakeNp())
@patch("recorder.sd")
def test_callback_pushes_audio_frames(mock_sd: MagicMock) -> None:
    mock_sd.InputStream.return_value = MagicMock()

    recorder = SoundDeviceRecorder(sample_rate=16000, channels=1, chunk_ms=100)
    q: Queue[AudioFrame | None] = Queue(maxsize=50)
    recorder.start(q)

    # Simulate callback invocation (like sounddevice would do)
    fake_data = _make_fake_audio_data(1600)  # 100ms at 16kHz
    recorder._on_audio(fake_data, frames=1600, time_info=None, status=None)

    assert not q.empty()
    frame = q.get_nowait()
    assert isinstance(frame, AudioFrame)
    assert frame.sample_rate == 16000
    assert frame.channels == 1
    assert len(frame.pcm16_bytes) == 1600 * 2  # 16-bit = 2 bytes per sample

    recorder.stop()


# ---------------------------------------------------------------
# Queue full - dropped chunks counting
# ---------------------------------------------------------------

@patch("recorder.np", _FakeNp())
@patch("recorder.sd")
def test_queue_full_increments_dropped_chunks(mock_sd: MagicMock) -> None:
    mock_sd.InputStream.return_value = MagicMock()

    recorder = SoundDeviceRecorder()
    q: Queue[AudioFrame | None] = Queue(maxsize=1)
    recorder.start(q)

    fake_data = _make_fake_audio_data(1600)

    # Fill the queue
    recorder._on_audio(fake_data, frames=1600, time_info=None, status=None)
    assert recorder.dropped_chunks == 0

    # This should be dropped
    recorder._on_audio(fake_data, frames=1600, time_info=None, status=None)
    assert recorder.dropped_chunks == 1

    recorder.stop()


# ---------------------------------------------------------------
# Stop emits sentinel
# ---------------------------------------------------------------

@patch("recorder.sd")
def test_stop_emits_sentinel(mock_sd: MagicMock) -> None:
    mock_sd.InputStream.return_value = MagicMock()

    recorder = SoundDeviceRecorder()
    q: Queue[AudioFrame | None] = Queue()
    recorder.start(q)
    recorder.stop()

    sentinel = q.get_nowait()
    assert sentinel is None


# ---------------------------------------------------------------
# No sounddevice installed
# ---------------------------------------------------------------

def test_start_raises_without_sounddevice(monkeypatch) -> None:  # noqa: ANN001
    import recorder as rec_mod
    monkeypatch.setattr(rec_mod, "sd", None)

    recorder = SoundDeviceRecorder()
    q: Queue[AudioFrame | None] = Queue()
    with pytest.raises(RuntimeError, match="sounddevice is not installed"):
        recorder.start(q)


# ---------------------------------------------------------------
# Callback after stop is a no-op
# ---------------------------------------------------------------

@patch("recorder.np", _FakeNp())
@patch("recorder.sd")
def test_callback_after_stop_is_noop(mock_sd: MagicMock) -> None:
    mock_sd.InputStream.return_value = MagicMock()

    recorder = SoundDeviceRecorder()
    q: Queue[AudioFrame | None] = Queue()
    recorder.start(q)
    recorder.stop()

    # Drain sentinel
    q.get_nowait()

    # Calling callback after stop should not push anything
    fake_data = _make_fake_audio_data(1600)
    recorder._on_audio(fake_data, frames=1600, time_info=None, status=None)
    assert q.empty()
