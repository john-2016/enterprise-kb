"""Provider 工厂 — 根据 ``provider_type`` 返回正确的客户端实例。

支持的 provider_type：
- openai_compat / minimax / deepseek / qwen / glm / ollama / local → OpenAICompatClient
- anthropic → AnthropicClient
- gemini → GeminiClient
"""
from __future__ import annotations

from typing import Protocol

from backend.services.model_clients.anthropic import AnthropicClient
from backend.services.model_clients.gemini import GeminiClient
from backend.services.model_clients.openai_compat import OpenAICompatClient


# 走 OpenAI 兼容协议的 provider_type（按业务枚举）
_OPENAI_COMPAT_TYPES = frozenset({
    "openai_compat",
    "minimax",
    "deepseek",
    "qwen",
    "glm",
    "ollama",
    "local",
})

# 原生 provider
_NATIVE_TYPES = frozenset({"anthropic", "gemini"})


class _ProviderLike(Protocol):
    """工厂需要的最小 provider 接口（duck typing）。"""
    provider_type: str
    api_base_url: str | None


def get_client(provider: _ProviderLike, decrypted_key: str):
    """根据 provider_type 构造对应 client。

    :param provider: 任何含 provider_type / api_base_url 属性的对象
    :param decrypted_key: 已用 Fernet 解密后的明文 API key
    :raises ValueError: 未知的 provider_type 或缺失 base_url
    """
    pt = provider.provider_type
    base_url = (provider.api_base_url or "").strip()

    if pt in _OPENAI_COMPAT_TYPES:
        if not base_url:
            raise ValueError(f"provider_type={pt} requires non-empty api_base_url")
        return OpenAICompatClient(api_key=decrypted_key, base_url=base_url)

    if pt == "anthropic":
        if not base_url:
            raise ValueError("anthropic provider requires non-empty api_base_url")
        return AnthropicClient(api_key=decrypted_key, base_url=base_url)

    if pt == "gemini":
        if not base_url:
            raise ValueError("gemini provider requires non-empty api_base_url")
        return GeminiClient(api_key=decrypted_key, base_url=base_url)

    raise ValueError(
        f"Unknown provider_type: {pt!r}. "
        f"Supported: {sorted(_OPENAI_COMPAT_TYPES | _NATIVE_TYPES)}"
    )
