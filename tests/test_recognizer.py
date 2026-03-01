"""Tests for DashscopeRecognizerAdapter."""

from __future__ import annotations

import time
from queue import Queue
import types

import recognizer as rec_mod
from models import AudioFrame, RecognitionEvent, RecognitionKind
from recognizer import DashscopeRecognizerAdapter, _ASRCallback


class _FakeRecognitionResult:
    @staticmethod
    def is_sentence_end(sentence: dict) -> bool:
        return bool(sentence.get("sentence_end"))


class _FakeResult:
    def __init__(self, text: str, sentence_end: bool) -> None:
        self._sentence = {"text": text, "sentence_end": sentence_end}

    def get_sentence(self) -> dict:
        return self._sentence


class _FakeRecognition:
    def __init__(self, **kwargs) -> None:  # noqa: ANN003
        self._callback = kwargs["callback"]

    def start(self) -> None:
        self._callback.on_open()

    def send_audio_frame(self, _data: bytes) -> None:
        self._callback.on_event(_FakeResult("partial", sentence_end=False))

    def stop(self) -> None:
        self._callback.on_event(_FakeResult("final", sentence_end=True))
        self._callback.on_complete()
        self._callback.on_close()


def _make_frame() -> AudioFrame:
    return AudioFrame(pcm16_bytes=b"\x00\x00" * 1600, sample_rate=16000, channels=1, timestamp_ms=0)


def _wait_for_terminal(events: list[RecognitionEvent], timeout: float = 2.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if any(e.kind in (RecognitionKind.FINAL.value, RecognitionKind.ERROR.value) for e in events):
            return
        time.sleep(0.02)


def test_asr_callback_emits_partial_and_final(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(rec_mod, "RecognitionResult", _FakeRecognitionResult)
    events: list[RecognitionEvent] = []
    callback = _ASRCallback(events.append)

    callback.on_event(_FakeResult("你好", sentence_end=False))
    callback.on_event(_FakeResult("你好世界", sentence_end=True))

    assert [e.kind for e in events] == [RecognitionKind.PARTIAL.value, RecognitionKind.FINAL.value]
    assert events[-1].text == "你好世界"


def test_asr_callback_on_complete_emits_final_when_missing_sentence_end(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(rec_mod, "RecognitionResult", _FakeRecognitionResult)
    events: list[RecognitionEvent] = []
    callback = _ASRCallback(events.append)

    callback.on_event(_FakeResult("半句结果", sentence_end=False))
    callback.on_complete()

    finals = [e for e in events if e.kind == RecognitionKind.FINAL.value]
    assert len(finals) == 1
    assert finals[0].text == "半句结果"


def test_asr_callback_ignores_no_valid_audio_error(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(rec_mod, "RecognitionResult", _FakeRecognitionResult)
    events: list[RecognitionEvent] = []
    callback = _ASRCallback(events.append)

    callback.on_error(types.SimpleNamespace(message="NO_VALID_AUDIO_ERROR"))

    assert events == []


def test_start_with_fake_recognition_emits_partial_and_final(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(rec_mod, "_HAS_DASHSCOPE", True)
    monkeypatch.setattr(rec_mod, "RecognitionResult", _FakeRecognitionResult)
    monkeypatch.setattr(rec_mod, "Recognition", _FakeRecognition)

    adapter = DashscopeRecognizerAdapter(api_key="test-key")
    q: Queue[AudioFrame | None] = Queue()
    q.put(_make_frame())
    q.put(None)
    events: list[RecognitionEvent] = []

    adapter.start(q, events.append)
    _wait_for_terminal(events)
    adapter.stop()

    assert any(e.kind == RecognitionKind.PARTIAL.value for e in events)
    finals = [e for e in events if e.kind == RecognitionKind.FINAL.value]
    assert len(finals) == 1
    assert finals[0].text == "final"


def test_missing_api_key_emits_auth_error(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(rec_mod, "_HAS_DASHSCOPE", True)
    monkeypatch.setattr(rec_mod, "Recognition", _FakeRecognition)
    monkeypatch.setattr(rec_mod, "RecognitionResult", _FakeRecognitionResult)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)

    adapter = DashscopeRecognizerAdapter(api_key="")
    q: Queue[AudioFrame | None] = Queue()
    q.put(_make_frame())
    q.put(None)
    events: list[RecognitionEvent] = []

    adapter.start(q, events.append)
    _wait_for_terminal(events)
    adapter.stop()

    errors = [e for e in events if e.kind == RecognitionKind.ERROR.value]
    assert len(errors) == 1
    assert errors[0].code == "AUTH_FAILED"


def test_dashscope_missing_emits_protocol_error(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(rec_mod, "_HAS_DASHSCOPE", False)

    adapter = DashscopeRecognizerAdapter(api_key="test-key")
    q: Queue[AudioFrame | None] = Queue()
    q.put(_make_frame())
    q.put(None)
    events: list[RecognitionEvent] = []

    adapter.start(q, events.append)
    _wait_for_terminal(events)
    adapter.stop()

    errors = [e for e in events if e.kind == RecognitionKind.ERROR.value]
    assert len(errors) == 1
    assert errors[0].code == "ASR_PROTOCOL_ERROR"


def test_start_error_maps_network_error(monkeypatch) -> None:  # noqa: ANN001
    class _StartErrorRecognition(_FakeRecognition):
        def start(self) -> None:
            raise ConnectionError("network timeout")

    monkeypatch.setattr(rec_mod, "_HAS_DASHSCOPE", True)
    monkeypatch.setattr(rec_mod, "Recognition", _StartErrorRecognition)
    monkeypatch.setattr(rec_mod, "RecognitionResult", _FakeRecognitionResult)

    adapter = DashscopeRecognizerAdapter(api_key="test-key")
    q: Queue[AudioFrame | None] = Queue()
    q.put(_make_frame())
    q.put(None)
    events: list[RecognitionEvent] = []

    adapter.start(q, events.append)
    _wait_for_terminal(events)
    adapter.stop()

    errors = [e for e in events if e.kind == RecognitionKind.ERROR.value]
    assert len(errors) == 1
    assert errors[0].code == "NETWORK_ERROR"


def test_start_error_maps_auth_failed(monkeypatch) -> None:  # noqa: ANN001
    class _StartErrorRecognition(_FakeRecognition):
        def start(self) -> None:
            raise RuntimeError("401 unauthorized api key")

    monkeypatch.setattr(rec_mod, "_HAS_DASHSCOPE", True)
    monkeypatch.setattr(rec_mod, "Recognition", _StartErrorRecognition)
    monkeypatch.setattr(rec_mod, "RecognitionResult", _FakeRecognitionResult)

    adapter = DashscopeRecognizerAdapter(api_key="bad")
    q: Queue[AudioFrame | None] = Queue()
    q.put(_make_frame())
    q.put(None)
    events: list[RecognitionEvent] = []

    adapter.start(q, events.append)
    _wait_for_terminal(events)
    adapter.stop()

    errors = [e for e in events if e.kind == RecognitionKind.ERROR.value]
    assert len(errors) == 1
    assert errors[0].code == "AUTH_FAILED"


def test_health_snapshot_updates_after_streaming(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(rec_mod, "_HAS_DASHSCOPE", True)
    monkeypatch.setattr(rec_mod, "RecognitionResult", _FakeRecognitionResult)
    monkeypatch.setattr(rec_mod, "Recognition", _FakeRecognition)

    adapter = DashscopeRecognizerAdapter(api_key="test-key")
    q: Queue[AudioFrame | None] = Queue()
    q.put(_make_frame())
    q.put(None)
    events: list[RecognitionEvent] = []

    before = adapter.get_health_snapshot()
    assert before["thread_alive"] is False
    assert before["last_event_at_ms"] == 0
    assert before["final_emitted"] is False
    assert before["connection_active"] is False
    assert before["queue_backlog"] == 0
    assert before["exception_count"] == 0

    adapter.start(q, events.append)
    _wait_for_terminal(events)
    adapter.stop()

    after = adapter.get_health_snapshot()
    assert after["thread_alive"] is False
    assert after["last_event_at_ms"] > 0
    assert after["final_emitted"] is True
    assert after["connection_active"] is False
    assert after["queue_backlog"] == 0
    assert after["exception_count"] == 0


def test_health_snapshot_counts_exceptions(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(rec_mod, "_HAS_DASHSCOPE", False)

    adapter = DashscopeRecognizerAdapter(api_key="test-key")
    q: Queue[AudioFrame | None] = Queue()
    q.put(_make_frame())
    q.put(None)
    events: list[RecognitionEvent] = []

    adapter.start(q, events.append)
    _wait_for_terminal(events)
    adapter.stop()

    snapshot = adapter.get_health_snapshot()
    assert snapshot["exception_count"] == 1
