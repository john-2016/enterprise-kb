"""UnifiedModelClient 协议 + 共享数据模型。

所有 provider 客户端（OpenAI 兼容 / Anthropic / Gemini）都实现这个协议，
让上层 ``ModelRouter`` / ``FallbackChain`` 不必关心具体 provider。
"""
from __future__ import annotations

from typing import Optional, Protocol, Sequence, runtime_checkable

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    """单条对话消息。"""

    role: str = Field(..., description="system / user / assistant")
    content: str = Field(..., description="消息文本")


class ChatResponse(BaseModel):
    """聊天响应。"""

    content: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    raw: Optional[dict] = Field(default=None, description="原始 provider 响应（调试用）")


class EmbedResponse(BaseModel):
    """嵌入响应。"""

    vectors: list[list[float]]
    model: str


@runtime_checkable
class UnifiedModelClient(Protocol):
    """统一的模型客户端接口。"""

    async def chat(
        self,
        messages: Sequence[ChatMessage],
        model: str,
        temperature: float,
        max_tokens: int,
        stream: bool = False,
    ) -> ChatResponse: ...

    async def embed(
        self,
        texts: Sequence[str],
        model: str,
    ) -> EmbedResponse: ...
