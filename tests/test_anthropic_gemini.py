"""Anthropic + Gemini 客户端测试。"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _mock_resp(status, json_data=None, text=""):
    m = MagicMock()
    m.status_code = status
    m.json = MagicMock(return_value=json_data or {})
    m.text = text
    return m


# ---------------------- Anthropic ----------------------

@pytest.fixture
def anthropic_client():
    from backend.services.model_clients.anthropic import AnthropicClient
    return AnthropicClient(
        api_key="sk-ant-test", base_url="https://api.anthropic.com"
    )


async def test_anthropic_chat_success(anthropic_client):
    mock = _mock_resp(200, {
        "content": [{"type": "text", "text": "hello from claude"}],
        "usage": {"input_tokens": 10, "output_tokens": 5},
    })
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock)):
        from backend.services.model_clients.base import ChatMessage
        resp = await anthropic_client.chat(
            [ChatMessage(role="user", content="hi")], "claude-3-5-sonnet", 0.7, 100
        )
    assert resp.content == "hello from claude"
    assert resp.input_tokens == 10


async def test_anthropic_chat_401_nonretryable(anthropic_client):
    mock = _mock_resp(401, text="invalid x-api-key")
    from backend.core.errors import NonRetryableError
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock)):
        from backend.services.model_clients.base import ChatMessage
        with pytest.raises(NonRetryableError):
            await anthropic_client.chat(
                [ChatMessage(role="user", content="hi")], "claude-3-5-sonnet", 0.7, 100
            )


async def test_anthropic_chat_529_retryable(anthropic_client):
    """529 = overloaded, 可重试。"""
    mock = _mock_resp(529, text="overloaded")
    from backend.core.errors import RetryableError
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock)):
        from backend.services.model_clients.base import ChatMessage
        with pytest.raises(RetryableError):
            await anthropic_client.chat(
                [ChatMessage(role="user", content="hi")], "claude-3-5-sonnet", 0.7, 100
            )


async def test_anthropic_embed_not_implemented(anthropic_client):
    """Anthropic 没有 embed API — 明确报错。"""
    with pytest.raises(NotImplementedError, match="embedding"):
        await anthropic_client.embed(["text"], "claude-3-5-sonnet")


async def test_anthropic_system_message_extracted(anthropic_client):
    """system 消息应被提取到顶层 system 字段，不进 messages。"""
    mock = _mock_resp(200, {
        "content": [{"type": "text", "text": "ok"}],
        "usage": {"input_tokens": 1, "output_tokens": 1},
    })
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock)) as mock_post:
        from backend.services.model_clients.base import ChatMessage
        await anthropic_client.chat(
            [
                ChatMessage(role="system", content="be concise"),
                ChatMessage(role="user", content="hi"),
            ],
            "claude-3-5-sonnet", 0.7, 100,
        )
    # 验证 body 里 system 字段存在，messages 里没有 system
    call_args = mock_post.call_args
    body = call_args.kwargs["json"]
    assert body["system"] == "be concise"
    assert all(m["role"] != "system" for m in body["messages"])


# ---------------------- Gemini ----------------------

@pytest.fixture
def gemini_client():
    from backend.services.model_clients.gemini import GeminiClient
    return GeminiClient(
        api_key="AIza-test", base_url="https://generativelanguage.googleapis.com"
    )


async def test_gemini_chat_success(gemini_client):
    mock = _mock_resp(200, {
        "candidates": [
            {"content": {"parts": [{"text": "hello from gemini"}]}}
        ],
        "usageMetadata": {"promptTokenCount": 7, "candidatesTokenCount": 4},
    })
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock)):
        from backend.services.model_clients.base import ChatMessage
        resp = await gemini_client.chat(
            [ChatMessage(role="user", content="hi")], "gemini-1.5-pro", 0.7, 100
        )
    assert resp.content == "hello from gemini"
    assert resp.input_tokens == 7


async def test_gemini_chat_403_nonretryable(gemini_client):
    mock = _mock_resp(403, text="API key not valid")
    from backend.core.errors import NonRetryableError
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock)):
        from backend.services.model_clients.base import ChatMessage
        with pytest.raises(NonRetryableError):
            await gemini_client.chat(
                [ChatMessage(role="user", content="hi")], "gemini-1.5-pro", 0.7, 100
            )


async def test_gemini_chat_429_retryable(gemini_client):
    mock = _mock_resp(429, text="quota exceeded")
    from backend.core.errors import RetryableError
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock)):
        from backend.services.model_clients.base import ChatMessage
        with pytest.raises(RetryableError):
            await gemini_client.chat(
                [ChatMessage(role="user", content="hi")], "gemini-1.5-pro", 0.7, 100
            )


async def test_gemini_malformed_response_raises_nonretryable(gemini_client):
    """响应结构异常 → NonRetryableError（重试也无用）。"""
    mock = _mock_resp(200, {"candidates": []})  # 缺 content.parts
    from backend.core.errors import NonRetryableError
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock)):
        from backend.services.model_clients.base import ChatMessage
        with pytest.raises(NonRetryableError, match="malformed"):
            await gemini_client.chat(
                [ChatMessage(role="user", content="hi")], "gemini-1.5-pro", 0.7, 100
            )


async def test_gemini_embed_success(gemini_client):
    mock = _mock_resp(200, {
        "embeddings": [
            {"values": [0.1, 0.2, 0.3]},
            {"values": [0.4, 0.5, 0.6]},
        ]
    })
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock)):
        resp = await gemini_client.embed(["t1", "t2"], "text-embedding-004")
    assert len(resp.vectors) == 2
    assert resp.vectors[0] == [0.1, 0.2, 0.3]


async def test_gemini_assistant_role_renamed(gemini_client):
    """assistant 角色应被改名为 model（Gemini 协议要求）。"""
    mock = _mock_resp(200, {
        "candidates": [{"content": {"parts": [{"text": "ok"}]}}],
    })
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock)) as mock_post:
        from backend.services.model_clients.base import ChatMessage
        await gemini_client.chat(
            [ChatMessage(role="user", content="hi"),
             ChatMessage(role="assistant", content="hello")],
            "gemini-1.5-pro", 0.7, 100,
        )
    body = mock_post.call_args.kwargs["json"]
    roles = [c["role"] for c in body["contents"]]
    assert "assistant" not in roles
    assert "model" in roles
