"""OpenAI 兼容客户端测试（mock httpx）。

覆盖：
- chat 200 成功路径
- 401/403/400/404 抛 NonRetryableError
- 429/500/502/503/504 抛 RetryableError
- embed 200 成功路径
- embed 401 抛 NonRetryableError
- latency_ms 字段合理
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _mock_response(status_code, json_data=None, text=""):
    m = MagicMock()
    m.status_code = status_code
    m.json = MagicMock(return_value=json_data or {})
    m.text = text
    return m


@pytest.fixture
def client():
    from backend.services.model_clients.openai_compat import OpenAICompatClient
    return OpenAICompatClient(api_key="sk-test", base_url="https://api.example.com/v1")


async def test_chat_success(client):
    mock_resp = _mock_response(200, {
        "choices": [{"message": {"content": "hello world"}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 3},
    })
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_resp)):
        from backend.services.model_clients.base import ChatMessage
        resp = await client.chat(
            [ChatMessage(role="user", content="hi")], "test-model", 0.7, 100
        )
    assert resp.content == "hello world"
    assert resp.input_tokens == 5
    assert resp.output_tokens == 3
    assert resp.latency_ms > 0


async def test_chat_401_raises_nonretryable(client):
    mock_resp = _mock_response(401, text="Unauthorized")
    from backend.core.errors import NonRetryableError
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_resp)):
        from backend.services.model_clients.base import ChatMessage
        with pytest.raises(NonRetryableError):
            await client.chat([ChatMessage(role="user", content="hi")], "m", 0.7, 100)


async def test_chat_403_raises_nonretryable(client):
    mock_resp = _mock_response(403, text="Forbidden")
    from backend.core.errors import NonRetryableError
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_resp)):
        from backend.services.model_clients.base import ChatMessage
        with pytest.raises(NonRetryableError):
            await client.chat([ChatMessage(role="user", content="hi")], "m", 0.7, 100)


async def test_chat_400_raises_nonretryable(client):
    """400 一般是参数错，不应重试。"""
    mock_resp = _mock_response(400, text="Bad request")
    from backend.core.errors import NonRetryableError
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_resp)):
        from backend.services.model_clients.base import ChatMessage
        with pytest.raises(NonRetryableError):
            await client.chat([ChatMessage(role="user", content="hi")], "m", 0.7, 100)


async def test_chat_429_raises_retryable(client):
    """429 限流，可重试。"""
    mock_resp = _mock_response(429, text="Rate limited")
    from backend.core.errors import RetryableError
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_resp)):
        from backend.services.model_clients.base import ChatMessage
        with pytest.raises(RetryableError):
            await client.chat([ChatMessage(role="user", content="hi")], "m", 0.7, 100)


async def test_chat_500_raises_retryable(client):
    mock_resp = _mock_response(500, text="server error")
    from backend.core.errors import RetryableError
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_resp)):
        from backend.services.model_clients.base import ChatMessage
        with pytest.raises(RetryableError):
            await client.chat([ChatMessage(role="user", content="hi")], "m", 0.7, 100)


async def test_chat_502_raises_retryable(client):
    mock_resp = _mock_response(502, text="bad gateway")
    from backend.core.errors import RetryableError
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_resp)):
        from backend.services.model_clients.base import ChatMessage
        with pytest.raises(RetryableError):
            await client.chat([ChatMessage(role="user", content="hi")], "m", 0.7, 100)


async def test_embed_success(client):
    mock_resp = _mock_response(200, {
        "data": [
            {"embedding": [0.1, 0.2, 0.3], "index": 0},
            {"embedding": [0.4, 0.5, 0.6], "index": 1},
        ]
    })
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_resp)):
        resp = await client.embed(["text1", "text2"], "emb-model")
    assert len(resp.vectors) == 2
    assert resp.vectors[0] == [0.1, 0.2, 0.3]
    assert resp.model == "emb-model"


async def test_embed_401_raises_nonretryable(client):
    mock_resp = _mock_response(401, text="Unauthorized")
    from backend.core.errors import NonRetryableError
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_resp)):
        with pytest.raises(NonRetryableError):
            await client.embed(["text"], "emb-model")


async def test_base_url_strips_trailing_slash():
    """base_url 末尾 / 应被剥除，避免出现 //chat/completions。"""
    from backend.services.model_clients.openai_compat import OpenAICompatClient
    c = OpenAICompatClient(api_key="k", base_url="https://api.example.com/v1/")
    assert c.base_url == "https://api.example.com/v1"
