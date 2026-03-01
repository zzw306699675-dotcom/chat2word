from __future__ import annotations

import sys
import types

from llm_adapter import QwenPolishAdapter


class _FakeDefaultHttpxClient:
    def __init__(self, **kwargs) -> None:  # noqa: ANN003
        self.kwargs = kwargs


class _FakeOpenAI:
    last_init: dict = {}
    last_create: dict = {}
    init_count = 0

    def __init__(self, **kwargs) -> None:  # noqa: ANN003
        _FakeOpenAI.last_init = kwargs
        _FakeOpenAI.init_count += 1
        self.chat = types.SimpleNamespace(
            completions=types.SimpleNamespace(create=self._create)
        )

    def _create(self, **kwargs):  # noqa: ANN003
        _FakeOpenAI.last_create = kwargs
        msg = types.SimpleNamespace(content="1. 优化后文本")
        choice = types.SimpleNamespace(message=msg)
        return types.SimpleNamespace(choices=[choice])


def _install_fake_openai(monkeypatch) -> None:  # noqa: ANN001
    _FakeOpenAI.last_init = {}
    _FakeOpenAI.last_create = {}
    _FakeOpenAI.init_count = 0
    fake_openai_mod = types.SimpleNamespace(
        OpenAI=_FakeOpenAI,
        DefaultHttpxClient=_FakeDefaultHttpxClient,
    )
    monkeypatch.setitem(sys.modules, "openai", fake_openai_mod)


def test_polish_text_speed_defaults(monkeypatch) -> None:  # noqa: ANN001
    _install_fake_openai(monkeypatch)

    adapter = QwenPolishAdapter(api_key="test-key")
    out = adapter.polish_text("测试")

    assert out == "1. 优化后文本"

    http_client = _FakeOpenAI.last_init["http_client"]
    assert isinstance(http_client, _FakeDefaultHttpxClient)
    assert http_client.kwargs["trust_env"] is False
    assert http_client.kwargs["timeout"] == 8.0

    assert _FakeOpenAI.last_init["max_retries"] == 0

    create_kwargs = _FakeOpenAI.last_create
    assert create_kwargs["temperature"] == 0.0
    assert create_kwargs["max_tokens"] == 220
    assert create_kwargs["extra_body"] == {"enable_thinking": False}
    assert create_kwargs["messages"][1]["content"].startswith("/no_think\n")


def test_polish_text_respects_proxy_opt_in(monkeypatch) -> None:  # noqa: ANN001
    _install_fake_openai(monkeypatch)

    adapter = QwenPolishAdapter(api_key="test-key", use_system_proxy=True, max_retries=1)
    _ = adapter.polish_text("测试")

    http_client = _FakeOpenAI.last_init["http_client"]
    assert http_client.kwargs["trust_env"] is True
    assert _FakeOpenAI.last_init["max_retries"] == 1


def test_client_reused_for_multiple_requests(monkeypatch) -> None:  # noqa: ANN001
    _install_fake_openai(monkeypatch)

    adapter = QwenPolishAdapter(api_key="test-key")
    _ = adapter.polish_text("第一次")
    _ = adapter.polish_text("第二次")

    assert _FakeOpenAI.init_count == 1
