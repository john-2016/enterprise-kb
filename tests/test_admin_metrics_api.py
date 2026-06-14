"""Phase 4 — Task 4.4: Admin Metrics Summary + Connectivity Test API.

Endpoints under test:
- GET  /api/v1/admin/metrics/summary?days=N
    聚合 ``ab_test_metrics`` 表（最近 N 天，按 model_id 分组），返回每模型的
    调用数 / 平均延迟 / token / 满意度，以及 satisfaction_rate 最高的 winner。
- POST /api/v1/admin/models/test
    Body: ``{provider_id, model_name, test_message?}``
    临时解密 provider key、构造客户端、发一条 chat；任何异常都返回
    ``{success: False, error: ...}`` 而非 500。

鉴权：均为 admin-only；普通 user 命中 → 403。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import select

from backend.models.ab_test import ABTestMetric, ABTestRule
from backend.models.model_config import ModelConfig
from backend.models.provider import ModelProvider
from backend.models.user import User
from tests.conftest import auth_header

SUMMARY_API = "/api/v1/admin/metrics/summary"
TEST_API = "/api/v1/admin/models/test"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _make_provider(client, admin_token, *, name: str = "p_metrics") -> int:
    body = {
        "name": name,
        "display_name": "Provider For Metrics",
        "provider_type": "openai_compat",
        "api_base_url": "https://api.example.com/v1",
        "api_key": "sk-metric-1234",
    }
    r = await client.post(
        "/api/v1/admin/providers", json=body, headers=auth_header(admin_token)
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _make_model(
    client,
    admin_token,
    provider_id: int,
    *,
    name: str,
    model_type: str = "chat",
) -> int:
    body = {
        "provider_id": provider_id,
        "model_name": name,
        "display_name": name,
        "model_type": model_type,
        "context_window": 128000,
        "is_default_chat": False,
        "is_default_emb": False,
        "extra_config": {},
    }
    r = await client.post(
        "/api/v1/admin/models", json=body, headers=auth_header(admin_token)
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _seed_metric(
    db_session,
    *,
    user_id: int,
    model_id: int,
    latency_ms: int,
    input_tokens: int,
    output_tokens: int,
    feedback: int | None,
    request_type: str = "chat",
    ab_rule_id: int | None = None,
    created_at: datetime | None = None,
) -> int:
    """直接 ORM 插入一条 metric（不走 API）。"""
    m = ABTestMetric(
        user_id=user_id,
        model_id=model_id,
        ab_rule_id=ab_rule_id,
        request_type=request_type,
        latency_ms=latency_ms,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        feedback=feedback,
        feedback_text=None,
    )
    if created_at is not None:
        m.created_at = created_at
    db_session.add(m)
    await db_session.commit()
    await db_session.refresh(m)
    return m.id


# ---------------------------------------------------------------------------
# AuthZ
# ---------------------------------------------------------------------------


async def test_non_admin_get_summary_returns_403(client, user_token):
    resp = await client.get(
        f"{SUMMARY_API}?days=7", headers=auth_header(user_token)
    )
    assert resp.status_code == 403


async def test_non_admin_post_test_returns_403(client, user_token):
    resp = await client.post(
        TEST_API,
        json={"provider_id": 1, "model_name": "m1"},
        headers=auth_header(user_token),
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Summary — aggregation correctness
# ---------------------------------------------------------------------------


async def test_summary_aggregates_metrics_by_model(
    client, admin_token, db_session, normal_user
):
    """两个模型各有不同 metric，summary 应正确聚合。"""
    pid = await _make_provider(client, admin_token, name="p_agg")
    m1 = await _make_model(client, admin_token, pid, name="alpha")
    m2 = await _make_model(client, admin_token, pid, name="beta")

    # alpha: 2 calls, avg latency = (100+200)/2 = 150, fb: +1, +1
    await _seed_metric(
        db_session,
        user_id=normal_user.id,
        model_id=m1,
        latency_ms=100,
        input_tokens=10,
        output_tokens=5,
        feedback=1,
    )
    await _seed_metric(
        db_session,
        user_id=normal_user.id,
        model_id=m1,
        latency_ms=200,
        input_tokens=20,
        output_tokens=10,
        feedback=1,
    )
    # beta: 1 call, latency=500, fb: -1
    await _seed_metric(
        db_session,
        user_id=normal_user.id,
        model_id=m2,
        latency_ms=500,
        input_tokens=30,
        output_tokens=15,
        feedback=-1,
    )

    resp = await client.get(
        f"{SUMMARY_API}?days=7", headers=auth_header(admin_token)
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["period_days"] == 7
    assert isinstance(data["models"], list)
    by_name = {row["model_name"]: row for row in data["models"]}

    alpha = by_name["alpha"]
    assert alpha["total_calls"] == 2
    assert alpha["avg_latency_ms"] == 150
    assert alpha["total_input_tokens"] == 30
    assert alpha["total_output_tokens"] == 15
    assert alpha["positive_feedback"] == 2
    assert alpha["negative_feedback"] == 0
    assert alpha["satisfaction_rate"] == 1.0

    beta = by_name["beta"]
    assert beta["total_calls"] == 1
    assert beta["avg_latency_ms"] == 500
    assert beta["positive_feedback"] == 0
    assert beta["negative_feedback"] == 1
    assert beta["satisfaction_rate"] == 0.0


async def test_summary_winner_is_highest_satisfaction(
    client, admin_token, db_session, normal_user
):
    """winner 字段应指向 satisfaction_rate 最高的模型名。"""
    pid = await _make_provider(client, admin_token, name="p_winner")
    m_low = await _make_model(client, admin_token, pid, name="low")
    m_high = await _make_model(client, admin_token, pid, name="DeepSeek-V3")

    # low: 2 👍 / 2 👎 → 0.5
    for _ in range(2):
        await _seed_metric(
            db_session,
            user_id=normal_user.id,
            model_id=m_low,
            latency_ms=100,
            input_tokens=1,
            output_tokens=1,
            feedback=1,
        )
    for _ in range(2):
        await _seed_metric(
            db_session,
            user_id=normal_user.id,
            model_id=m_low,
            latency_ms=100,
            input_tokens=1,
            output_tokens=1,
            feedback=-1,
        )

    # high: 3 👍 / 1 👎 → 0.75
    for _ in range(3):
        await _seed_metric(
            db_session,
            user_id=normal_user.id,
            model_id=m_high,
            latency_ms=100,
            input_tokens=1,
            output_tokens=1,
            feedback=1,
        )
    await _seed_metric(
        db_session,
        user_id=normal_user.id,
        model_id=m_high,
        latency_ms=100,
        input_tokens=1,
        output_tokens=1,
        feedback=-1,
    )

    resp = await client.get(
        f"{SUMMARY_API}?days=7", headers=auth_header(admin_token)
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["winner"] == "DeepSeek-V3"
    # 0.75 > 0.5
    by_name = {row["model_name"]: row for row in data["models"]}
    assert by_name["DeepSeek-V3"]["satisfaction_rate"] > by_name["low"]["satisfaction_rate"]


async def test_summary_no_feedback_returns_zero_rate(
    client, admin_token, db_session, normal_user
):
    """所有 metric 都未反馈时，satisfaction_rate 应为 0（分母=0）。"""
    pid = await _make_provider(client, admin_token, name="p_nofb")
    m1 = await _make_model(client, admin_token, pid, name="nofb")
    await _seed_metric(
        db_session,
        user_id=normal_user.id,
        model_id=m1,
        latency_ms=100,
        input_tokens=1,
        output_tokens=1,
        feedback=None,
    )
    resp = await client.get(
        f"{SUMMARY_API}?days=7", headers=auth_header(admin_token)
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    by_name = {row["model_name"]: row for row in data["models"]}
    assert by_name["nofb"]["positive_feedback"] == 0
    assert by_name["nofb"]["negative_feedback"] == 0
    assert by_name["nofb"]["satisfaction_rate"] == 0.0
    # 没有 winner
    assert data["winner"] is None


async def test_summary_days_filter_excludes_old_metrics(
    client, admin_token, db_session, normal_user
):
    """days=1 时只聚合最近 1 天内的 metric（更早的应被 SQL 过滤）。"""
    pid = await _make_provider(client, admin_token, name="p_days")
    m_recent = await _make_model(client, admin_token, pid, name="recent")
    m_old = await _make_model(client, admin_token, pid, name="old")

    now = datetime.utcnow()
    # recent: 在窗口内
    await _seed_metric(
        db_session,
        user_id=normal_user.id,
        model_id=m_recent,
        latency_ms=100,
        input_tokens=1,
        output_tokens=1,
        feedback=1,
        created_at=now,
    )
    # old: 10 天前
    await _seed_metric(
        db_session,
        user_id=normal_user.id,
        model_id=m_old,
        latency_ms=100,
        input_tokens=1,
        output_tokens=1,
        feedback=1,
        created_at=now - timedelta(days=10),
    )

    resp = await client.get(
        f"{SUMMARY_API}?days=1", headers=auth_header(admin_token)
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    names = {row["model_name"] for row in data["models"]}
    assert "recent" in names
    assert "old" not in names


# ---------------------------------------------------------------------------
# Connectivity Test
# ---------------------------------------------------------------------------


def _mock_response(status_code: int, json_data=None, text: str = ""):
    m = MagicMock()
    m.status_code = status_code
    m.json = MagicMock(return_value=json_data or {})
    m.text = text
    return m


def _mock_async_client(mock_resp):
    """构造一个 mock httpx.AsyncClient 实例（让 ``async with httpx.AsyncClient(...) as c`` 正常工作）。

    之前直接 ``patch("httpx.AsyncClient.post", ...)`` 在 ASGI 下会让 ``r.content`` 变 MagicMock，
    所以必须 mock 整个 AsyncClient 类（保留 __aenter__/__aexit__ 协议）。
    """
    instance = MagicMock()
    instance.post = AsyncMock(return_value=mock_resp)
    instance.__aenter__ = AsyncMock(return_value=instance)
    instance.__aexit__ = AsyncMock(return_value=False)
    return instance


async def test_connectivity_test_success_returns_true(
    client, admin_token
):
    """mock httpx 返回 200 + 合法 chat payload → success=True。"""
    pid = await _make_provider(client, admin_token, name="p_ok")
    # 创建一个 model（不强制，但语义上更接近真实路径）
    await _make_model(client, admin_token, pid, name="gpt-4o")

    mock_resp = _mock_response(
        200,
        {
            "choices": [{"message": {"content": "hello"}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3},
        },
    )
    mock_client = _mock_async_client(mock_resp)
    with patch("httpx.AsyncClient", return_value=mock_client):
        body = {
            "provider_id": pid,
            "model_name": "gpt-4o",
            "test_message": "ping",
        }
        resp = await client.post(
            TEST_API, json=body, headers=auth_header(admin_token)
        )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["success"] is True
    assert data["model"] == "gpt-4o"
    assert isinstance(data["latency_ms"], int)
    assert data["latency_ms"] >= 0
    assert "error" not in data or data.get("error") is None


async def test_connectivity_test_failure_returns_false_no_500(
    client, admin_token
):
    """provider 调用失败时返回 success=False + error，绝不 500。"""
    pid = await _make_provider(client, admin_token, name="p_fail")

    mock_resp = _mock_response(401, text="Unauthorized")
    mock_client = _mock_async_client(mock_resp)
    with patch("httpx.AsyncClient", return_value=mock_client):
        body = {"provider_id": pid, "model_name": "m"}
        resp = await client.post(
            TEST_API, json=body, headers=auth_header(admin_token)
        )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["success"] is False
    assert "error" in data
    assert isinstance(data["error"], str) and data["error"]
    assert "model" not in data or data.get("model") is None


async def test_connectivity_test_provider_not_found_returns_404(
    client, admin_token
):
    """不存在的 provider_id 应返回 404。"""
    body = {"provider_id": 999999, "model_name": "m"}
    resp = await client.post(
        TEST_API, json=body, headers=auth_header(admin_token)
    )
    assert resp.status_code == 404
