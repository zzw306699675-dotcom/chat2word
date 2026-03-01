"""LLM adapter for polishing recognized text with DashScope compatible OpenAI API."""

from __future__ import annotations

import os
from typing import Any

DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "qwen3.5-plus"
DEFAULT_TIMEOUT_S = 8.0
DEFAULT_MAX_TOKENS = 220

SYSTEM_PROMPT = (
    "你是中文文本润色助手。保持用户原始意图不变，不新增事实。"
    "优化语言清晰度与逻辑结构，优先整理为 1、2、3 的编号形式。"
    "只输出润色后的最终文本，不要解释。"
)


class QwenPolishAdapter:
    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_MODEL,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        use_system_proxy: bool = False,
        max_retries: int = 0,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        disable_thinking: bool = True,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url
        self._model = model
        self._timeout_s = timeout_s
        self._use_system_proxy = use_system_proxy
        self._max_retries = max_retries
        self._max_tokens = max_tokens
        self._disable_thinking = disable_thinking

        self._client = None
        self._client_api_key = ""

    def polish_text(self, text: str) -> str:
        content = text.strip()
        if not content:
            raise ValueError("empty text")

        api_key = self._api_key or os.getenv("DASHSCOPE_API_KEY", "")
        if not api_key:
            raise RuntimeError("No API key configured for LLM")

        client = self._get_client(api_key)

        request_kwargs: dict[str, Any] = {
            "model": self._model,
            "temperature": 0.0,
            "max_tokens": self._max_tokens,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": _prepare_user_prompt(content, disable_thinking=self._disable_thinking),
                },
            ],
        }
        if self._disable_thinking:
            # Qwen OpenAI-compatible mode supports toggling reasoning via
            # extra_body.enable_thinking. Force it off for faster responses.
            request_kwargs["extra_body"] = {"enable_thinking": False}

        response = client.chat.completions.create(**request_kwargs)

        polished = _extract_text(response)
        if not polished:
            raise RuntimeError("LLM returned empty content")
        return polished

    def _get_client(self, api_key: str):
        if self._client is not None and self._client_api_key == api_key:
            return self._client

        try:
            from openai import DefaultHttpxClient, OpenAI
        except Exception as exc:  # pragma: no cover
            raise RuntimeError(f"openai package is not installed: {exc}") from exc

        http_client = DefaultHttpxClient(
            timeout=self._timeout_s,
            trust_env=self._use_system_proxy,
        )
        self._client = OpenAI(
            api_key=api_key,
            base_url=self._base_url,
            timeout=self._timeout_s,
            max_retries=self._max_retries,
            http_client=http_client,
        )
        self._client_api_key = api_key
        return self._client


def _prepare_user_prompt(text: str, *, disable_thinking: bool) -> str:
    if not disable_thinking:
        return text
    # Qwen supports /no_think in prompt-level control.
    return f"/no_think\n{text}"


def _extract_text(response: Any) -> str:
    choices = getattr(response, "choices", None) or []
    if not choices:
        return ""

    message = getattr(choices[0], "message", None)
    if message is None:
        return ""

    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            text = ""
            if isinstance(item, dict):
                text = str(item.get("text", ""))
            else:
                text = str(getattr(item, "text", ""))
            if text:
                parts.append(text)
        return "\n".join(parts).strip()
    return str(content).strip()
