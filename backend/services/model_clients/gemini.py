"""Google Gemini 原生客户端。

协议：POST {base}/v1beta/models/{model}:generateContent?key={api_key}
Body: { contents: [{ parts: [{ text: "..." }] }], generationConfig: { temperature, maxOutputTokens } }
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


class GeminiClient:
    """Google Gemini API 客户端。"""

    def __init__(self, api_key: str, base_url: str, timeout: float = 30.0):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _classify_status(self, status: int, text: str) -> Exception:
        snippet = text[:200] if text else ""
        if status in (400, 401, 403, 404, 422):
            return NonRetryableError(f"gemini chat failed: {status} {snippet}")
        if status in (429, 500, 502, 503, 504):
            return RetryableError(f"gemini retryable: {status}")
        return NonRetryableError(f"gemini unexpected status: {status} {snippet}")

    def _convert_messages(self, messages: Sequence[ChatMessage]) -> tuple[str, list[dict]]:
        """Gemini 用 system_instruction + contents[role=user/model] 结构。

        返回: (system_instruction_text_or_empty, contents_list)
        """
        system_parts = [m.content for m in messages if m.role == "system"]
        system_text = "\n\n".join(system_parts) if system_parts else ""
        contents = []
        for m in messages:
            if m.role == "system":
                continue
            # Gemini 用 "model" 而不是 "assistant"
            role = "model" if m.role == "assistant" else "user"
            contents.append({"role": role, "parts": [{"text": m.content}]})
        return system_text, contents

    async def chat(
        self,
        messages: Sequence[ChatMessage],
        model: str,
        temperature: float,
        max_tokens: int,
        stream: bool = False,
    ) -> ChatResponse:
        system_text, contents = self._convert_messages(messages)
        payload = {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            },
        }
        if system_text:
            payload["systemInstruction"] = {"parts": [{"text": system_text}]}

        url = f"{self.base_url}/v1beta/models/{model}:generateContent"
        params = {"key": self.api_key}
        headers = {"Content-Type": "application/json"}

        t0 = time.perf_counter()
        async with httpx.AsyncClient(timeout=self.timeout) as c:
            r = await c.post(url, json=payload, params=params, headers=headers)
        latency_ms = int((time.perf_counter() - t0) * 1000)

        if r.status_code != 200:
            raise self._classify_status(r.status_code, r.text)

        data = r.json()
        # Gemini 响应: candidates[0].content.parts[0].text
        try:
            content = data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError) as e:
            raise NonRetryableError(f"gemini response malformed: {data}") from e
        usage = data.get("usageMetadata", {})
        return ChatResponse(
            content=content,
            input_tokens=usage.get("promptTokenCount", 0),
            output_tokens=usage.get("candidatesTokenCount", 0),
            latency_ms=latency_ms,
            raw=data,
        )

    async def embed(self, texts: Sequence[str], model: str) -> EmbedResponse:
        """Gemini embed: POST /v1beta/models/{embed_model}:batchEmbedContents"""
        url = f"{self.base_url}/v1beta/models/{model}:batchEmbedContents"
        payload = {
            "requests": [
                {"model": f"models/{model}", "content": {"parts": [{"text": t}]}}
                for t in texts
            ]
        }
        params = {"key": self.api_key}
        headers = {"Content-Type": "application/json"}

        async with httpx.AsyncClient(timeout=self.timeout) as c:
            r = await c.post(url, json=payload, params=params, headers=headers)

        if r.status_code != 200:
            raise self._classify_status(r.status_code, r.text)

        data = r.json()
        vectors = [e["values"] for e in data.get("embeddings", [])]
        return EmbedResponse(vectors=vectors, model=model)
