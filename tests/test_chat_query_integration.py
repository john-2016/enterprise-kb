"""Phase 4 — Task 4.6: ``/chat/query`` 接入 ModelRouter。

约束（v1.0 业务兼容）：
- ``answer`` / ``sources`` / ``tokens_used`` 三个字段**完全不动**（值、类型、顺序）
- 在响应里 **append** 三个字段：
    - ``model_used: {id, name, provider}``
    - ``latency_ms: int``
    - ``tokens: {input: int, output: int}``
- 一次成功的 chat 调用要落一条 ``ab_test_metrics`` 记录
- 所有模型都失败（``AllModelsFailedError``）→ 500，而不是 200

mock 模式：mock 整个 ``httpx.AsyncClient`` 类，**不**单独 patch ``.post`` 方法
（ASGI 下 r.content 会变成 MagicMock）。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import select

from backend.models.ab_test import ABTestMetric
from backend.routers import chat as chat_module
from tests.conftest import auth_header


QUERY_API = "/api/v1/chat/query"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _make_provider(client, admin_token, *, name: str) -> int:
    body = {
        "name": name,
        "display_name": "Provider For Query",
        "provider_type": "openai_compat",
        "api_base_url": "https://api.example.com/v1",
        "api_key": "***",
    }
    r = await client.post(
        "/api/v1/admin/providers", json=body, headers=auth_header(admin_token)
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _make_chat_model(
    client, admin_token, provider_id: int, *, name: str, is_default: bool = True
) -> int:
    body = {
        "provider_id": provider_id,
        "model_name": name,
        "display_name": name,
        "model_type": "chat",
        "context_window": 128000,
        "is_default_chat": is_default,
        "is_default_emb": False,
        "extra_config": {},
    }
    r = await client.post(
        "/api/v1/admin/models", json=body, headers=auth_header(admin_token)
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _mock_response(status_code: int, json_data=None, text: str = ""):
    m = MagicMock()
    m.status_code = status_code
    m.json = MagicMock(return_value=json_data or {})
    m.text = text
    return m


def _mock_async_client(mock_resp):
    """构造一个 mock httpx.AsyncClient 实例。

    关键点：必须 mock 整个 ``AsyncClient`` 类（而不是 ``.post`` 方法），
    否则在 ASGI 下 ``r.content`` 会变成 MagicMock，data 解析失败。
    """
    instance = MagicMock()
    instance.post = AsyncMock(return_value=mock_resp)
    instance.__aenter__ = AsyncMock(return_value=instance)
    instance.__aexit__ = AsyncMock(return_value=False)
    return instance


def _stub_v1_services():
    """把 chat 模块里的 ``_embedder`` / ``_vector_store`` 换成 mock。

    让 query endpoint 不会真去调 httpx 拿 embedding。
    """
    # embedder.embed_text → 返回一个 dummy 向量
    embedder = MagicMock()
    embedder.embed_text = AsyncMock(return_value=[0.0] * 8)
    # store.search → 空结果（无所谓，测试不关心 sources 内容）
    store = MagicMock()
    store.search = MagicMock(return_value=[])
    chat_module._embedder = embedder
    chat_module._vector_store = store
    chat_module._rag_service = MagicMock()
    return embedder, store


# ---------------------------------------------------------------------------
# 1) /chat/query 响应 append model_used 字段
# ---------------------------------------------------------------------------


async def test_query_response_includes_model_used(
    client, admin_token, user_token
):
    """改造后的 /chat/query 响应里包含 ``model_used`` 子对象。"""
    _stub_v1_services()
    pid = await _make_provider(client, admin_token, name="p_query_ok")
    mid = await _make_chat_model(client, admin_token, pid, name="gpt-4o-mini")

    mock_resp = _mock_response(
        200,
        {
            "choices": [{"message": {"content": "model-routed answer"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        },
    )
    mock_client = _mock_async_client(mock_resp)
    with patch("httpx.AsyncClient", return_value=mock_client):
        resp = await client.post(
            QUERY_API,
            json={"question": "What is RAG?"},
            headers=auth_header(user_token),
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    # 现有字段必须保留
    assert "answer" in data
    assert "sources" in data
    assert "tokens_used" in data
    assert data["answer"] == "model-routed answer"
    # 追加字段
    assert "model_used" in data
    mu = data["model_used"]
    assert mu["id"] == mid
    assert mu["name"] == "gpt-4o-mini"
    assert mu["provider"] == "p_query_ok"
    # latency_ms / tokens
    assert isinstance(data["latency_ms"], int)
    assert data["latency_ms"] >= 0
    assert data["tokens"] == {"input": 10, "output": 5}


# ---------------------------------------------------------------------------
# 2) 成功调用会落一条 ABTestMetric
# ---------------------------------------------------------------------------


async def test_query_writes_ab_test_metric(
    client, admin_token, user_token, db_session, normal_user
):
    """一次成功的 chat 应该往 ab_test_metrics 写一条记录。"""
    _stub_v1_services()
    pid = await _make_provider(client, admin_token, name="p_metric_ok")
    mid = await _make_chat_model(client, admin_token, pid, name="metric-m")

    # baseline: 0 metrics
    base = (
        await db_session.execute(select(ABTestMetric))
    ).scalars().all()
    base_count = len(base)

    mock_resp = _mock_response(
        200,
        {
            "choices": [{"message": {"content": "x"}}],
            "usage": {"prompt_tokens": 7, "completion_tokens": 3},
        },
    )
    mock_client = _mock_async_client(mock_resp)
    with patch("httpx.AsyncClient", return_value=mock_client):
        resp = await client.post(
            QUERY_API,
            json={"question": "ping"},
            headers=auth_header(user_token),
        )
    assert resp.status_code == 200, resp.text

    # DB 应该多一条
    all_rows = (
        await db_session.execute(select(ABTestMetric))
    ).scalars().all()
    assert len(all_rows) == base_count + 1

    new_row = all_rows[-1]
    assert new_row.user_id == normal_user.id
    assert new_row.model_id == mid
    assert new_row.request_type == "chat"
    assert new_row.input_tokens == 7
    assert new_row.output_tokens == 3
    assert isinstance(new_row.latency_ms, int)
    assert new_row.latency_ms >= 0
    # 默认没有 ab_rule（无 ABTestRule 启用），所以可以是 None
    assert new_row.ab_rule_id is None or isinstance(new_row.ab_rule_id, int)


# ---------------------------------------------------------------------------
# 3) 所有模型失败 → 500
# ---------------------------------------------------------------------------


async def test_query_returns_500_when_all_models_fail(
    client, admin_token, user_token
):
    """所有模型都抛 NonRetryableError → FallbackChain 抛 AllModelsFailedError → 500。"""
    _stub_v1_services()
    pid = await _make_provider(client, admin_token, name="p_query_fail")
    await _make_chat_model(client, admin_token, pid, name="broken")

    # 401 → NonRetryableError → 单模型无 fallback → AllModelsFailedError
    mock_resp = _mock_response(401, text="Unauthorized")
    mock_client = _mock_async_client(mock_resp)
    with patch("httpx.AsyncClient", return_value=mock_client):
        resp = await client.post(
            QUERY_API,
            json={"question": "boom"},
            headers=auth_header(user_token),
        )

    assert resp.status_code in (500, 503), resp.text
    # 错误信息应该是友好的（提到"all models"或类似）
    detail = resp.json().get("detail", "")
    assert isinstance(detail, str) and detail
