"""Anthropic Claude 原生客户端。

协议：POST {base}/v1/messages
Header: x-api-key, anthropic-version
Body: { model, messages, max_tokens, temperature }
"""
from __future__ import annotations

import time
from typing import Sequence

import httpx

from backend.core.errors import NonRetryableError, RetryableError
from backend.services.model_clients.base import (
    ChatMessage,
    ChatResponse,
)


class AnthropicClient:
    """Anthropic Claude 原生 API 客户端。"""

    DEFAULT_API_VERSION = "2023-06-01"

    def __init__(self, api_key: str, base_url: str, timeout: float = 30.0,
                 api_version: str = DEFAULT_API_VERSION):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.api_version = api_version

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self.api_key,
            "anthropic-version": self.api_version,
            "Content-Type": "application/json",
        }

    def _classify_status(self, status: int, text: str) -> Exception:
        snippet = text[:200] if text else ""
        # 401/403 → 鉴权；400/422 → 参数；404 → 路径错
        if status in (400, 401, 403, 404, 422):
            return NonRetryableError(f"anthropic chat failed: {status} {snippet}")
        if status in (429, 500, 502, 503, 504, 529):  # 529 = overloaded
            return RetryableError(f"anthropic retryable: {status}")
        return NonRetryableError(f"anthropic unexpected status: {status} {snippet}")

    async def chat(
        self,
        messages: Sequence[ChatMessage],
        model: str,
        temperature: float,
        max_tokens: int,
        stream: bool = False,
    ) -> ChatResponse:
        # Anthropic 协议：system 消息单独放最前面，其余是 user/assistant
        system_parts = [m.content for m in messages if m.role == "system"]
        convo = [{"role": m.role, "content": m.content}
                 for m in messages if m.role != "system"]
        payload = {
            "model": model,
            "messages": convo,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system_parts:
            payload["system"] = "\n\n".join(system_parts)

        url = f"{self.base_url}/v1/messages"
        t0 = time.perf_counter()
        async with httpx.AsyncClient(timeout=self.timeout) as c:
            r = await c.post(url, json=payload, headers=self._headers())
        latency_ms = int((time.perf_counter() - t0) * 1000)

        if r.status_code != 200:
            raise self._classify_status(r.status_code, r.text)

        data = r.json()
        # Anthropic 响应: content=[{"type": "text", "text": "..."}]
        content = ""
        for block in data.get("content", []):
            if block.get("type") == "text":
                content += block.get("text", "")
        usage = data.get("usage", {})
        return ChatResponse(
            content=content,
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            latency_ms=latency_ms,
            raw=data,
        )

    async def embed(self, texts: Sequence[str], model: str) -> None:
        """Anthropic 没有原生 embed API — 调用应报错（业务层应走 OpenAI 兼容的 embed provider）。"""
        raise NotImplementedError(
            "Anthropic does not provide embedding API. Use an OpenAI-compatible provider for embeddings."
        )
