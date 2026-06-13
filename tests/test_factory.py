"""Provider factory 路由测试。

验证 ``get_client(provider, decrypted_key)`` 根据 ``provider_type`` 返回正确的客户端类。
"""
from types import SimpleNamespace

import pytest


def _make_provider(provider_type, base_url="https://example.com/v1"):
    """构造一个最小可用的 ModelProvider-like 对象。"""
    p = SimpleNamespace(
        id=1,
        name="test",
        provider_type=provider_type,
        api_base_url=base_url,
        api_key_enc=b"encrypted",
    )
    return p


def test_factory_routes_to_openai_compat():
    from backend.services.model_clients.factory import get_client
    from backend.services.model_clients.openai_compat import OpenAICompatClient
    p = _make_provider("openai_compat")
    client = get_client(p, "sk-test")
    assert isinstance(client, OpenAICompatClient)


def test_factory_routes_minimax_to_openai_compat():
    """minimax 走 OpenAI 兼容协议。"""
    from backend.services.model_clients.factory import get_client
    from backend.services.model_clients.openai_compat import OpenAICompatClient
    p = _make_provider("minimax", base_url="https://api.minimaxi.com/v1")
    client = get_client(p, "sk-test")
    assert isinstance(client, OpenAICompatClient)
    assert client.base_url == "https://api.minimaxi.com/v1"


def test_factory_routes_deepseek_to_openai_compat():
    from backend.services.model_clients.factory import get_client
    from backend.services.model_clients.openai_compat import OpenAICompatClient
    p = _make_provider("deepseek", base_url="https://api.deepseek.com/v1")
    client = get_client(p, "sk-test")
    assert isinstance(client, OpenAICompatClient)


def test_factory_routes_anthropic():
    from backend.services.model_clients.factory import get_client
    from backend.services.model_clients.anthropic import AnthropicClient
    p = _make_provider("anthropic", base_url="https://api.anthropic.com")
    client = get_client(p, "sk-ant-test")
    assert isinstance(client, AnthropicClient)


def test_factory_routes_gemini():
    from backend.services.model_clients.factory import get_client
    from backend.services.model_clients.gemini import GeminiClient
    p = _make_provider("gemini", base_url="https://generativelanguage.googleapis.com")
    client = get_client(p, "AIza-test")
    assert isinstance(client, GeminiClient)


def test_factory_routes_ollama_local():
    """Ollama 走 OpenAI 兼容（/v1/chat/completions）。"""
    from backend.services.model_clients.factory import get_client
    from backend.services.model_clients.openai_compat import OpenAICompatClient
    p = _make_provider("ollama", base_url="http://localhost:11434/v1")
    client = get_client(p, "ollama")
    assert isinstance(client, OpenAICompatClient)


def test_factory_unknown_provider_type_raises():
    from backend.services.model_clients.factory import get_client
    p = _make_provider("unknown_future_provider")
    with pytest.raises(ValueError, match="Unknown provider_type"):
        get_client(p, "sk-test")


def test_factory_empty_api_base_url_raises_for_native():
    """Anthropic / Gemini 必须有 base_url。"""
    from backend.services.model_clients.factory import get_client
    p = _make_provider("anthropic", base_url="")
    with pytest.raises(ValueError, match="api_base_url"):
        get_client(p, "sk-test")
