"""OpenAI 兼容客户端 — 覆盖 OpenAI / MiniMax / DeepSeek / Qwen / GLM / 本地 Ollama 等。

特点：
- 复用 OpenAI HTTP 协议（/chat/completions, /embeddings）
- 错误分类：401/403/400/404 → NonRetryable；429/5xx → Retryable
- 不做自动重试（重试由上层 FallbackChain 统一管理）
"""
from __future__ import annotations

import time
from typing import Sequence

import httpx

from backend.core.errors import NonRetryableError, RetryableError
from backend.services.model_clients.base import (
    ChatMessage,
    ChatResponse,
    EmbedResponse,
)

# 错误分类表
_NONRETRYABLE_CODES = frozenset({400, 401, 403, 404, 422})
_RETRYABLE_CODES = frozenset({429, 500, 502, 503, 504})


class OpenAICompatClient:
    """OpenAI 兼容 HTTP 客户端。"""

    def __init__(self, api_key: str, base_url: str, timeout: float = 30.0):
        self.api_key = api_key
        # 末尾 / 剥除，避免路径变 //chat/completions
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _classify_status(self, status: int, text: str) -> Exception:
        """HTTP 状态码 → 错误类型。"""
        snippet = text[:200] if text else ""
        if status in _NONRETRYABLE_CODES:
            return NonRetryableError(f"chat failed: {status} {snippet}")
        if status in _RETRYABLE_CODES:
            return RetryableError(f"chat retryable: {status}")
        # 未知状态码默认按 non-retryable 处理（避免无限重试）
        return NonRetryableError(f"chat unexpected status: {status} {snippet}")

    async def chat(
        self,
        messages: Sequence[ChatMessage],
        model: str,
        temperature: float,
        max_tokens: int,
        stream: bool = False,
    ) -> ChatResponse:
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": model,
            "messages": [m.model_dump() for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream,
        }
        t0 = time.perf_counter()
        async with httpx.AsyncClient(timeout=self.timeout) as c:
            r = await c.post(url, json=payload, headers=self._headers())
        latency_ms = int((time.perf_counter() - t0) * 1000)

        if r.status_code != 200:
            raise self._classify_status(r.status_code, r.text)

        data = r.json()
        content = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        return ChatResponse(
            content=content,
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            latency_ms=latency_ms,
            raw=data,
        )

    async def embed(self, texts: Sequence[str], model: str) -> EmbedResponse:
        url = f"{self.base_url}/embeddings"
        payload = {"model": model, "input": list(texts)}
        async with httpx.AsyncClient(timeout=self.timeout) as c:
            r = await c.post(url, json=payload, headers=self._headers())

        if r.status_code != 200:
            raise self._classify_status(r.status_code, r.text)

        data = r.json()
        vectors = [d["embedding"] for d in data["data"]]
        return EmbedResponse(vectors=vectors, model=model)
