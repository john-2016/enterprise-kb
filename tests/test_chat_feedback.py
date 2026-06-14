"""Phase 4 — Task 4.5: Chat Feedback API.

Endpoint under test: ``POST /api/v1/chat/feedback``

Request body::

    {
        "metric_id": int,
        "feedback":  -1 | 0 | 1,
        "feedback_text": str | None
    }

Response::

    {"success": True, "metric_id": int}

Behaviour:
- Any logged-in user (``Depends(get_current_user)``) can post.
- The metric must exist (else 404) and belong to the calling user (else 403).
- On success, ``ab_test_metrics.feedback`` and ``ab_test_metrics.feedback_text``
  are updated and the new value is visible via DB.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from backend.core.crypto import encrypt_key
from backend.models.ab_test import ABTestMetric
from backend.models.model_config import ModelConfig
from backend.models.provider import ModelProvider
from backend.models.user import User
from tests.conftest import auth_header


FEEDBACK_API = "/api/v1/chat/feedback"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _seed_model(db_session) -> int:
    """Seed one ModelProvider + ModelConfig directly via ORM; return model id."""
    provider = ModelProvider(
        name=f"p_fb_{id(object())}",
        display_name="Provider For Feedback Tests",
        provider_type="openai_compat",
        api_base_url="https://api.example.com/v1",
        api_key_enc=encrypt_key("***"),
        extra_config={},
        enabled=True,
        is_builtin=False,
    )
    db_session.add(provider)
    await db_session.flush()

    model = ModelConfig(
        provider_id=provider.id,
        model_name="fb-model",
        display_name="FB Model",
        model_type="chat",
        context_window=128000,
        extra_config={},
    )
    db_session.add(model)
    await db_session.flush()
    return model.id


async def _seed_metric(
    db_session,
    *,
    user_id: int,
    model_id: int,
) -> int:
    """Seed a single ABTestMetric directly via ORM; return its id."""
    m = ABTestMetric(
        user_id=user_id,
        model_id=model_id,
        ab_rule_id=None,
        request_type="chat",
        latency_ms=10,
        input_tokens=1,
        output_tokens=1,
        feedback=None,
        feedback_text=None,
    )
    db_session.add(m)
    await db_session.flush()
    return m.id


# ---------------------------------------------------------------------------
# 1) owner can submit positive feedback — metric.feedback updates to 1
# ---------------------------------------------------------------------------


async def test_owner_can_post_positive_feedback(
    client, user_token, normal_user, db_session
):
    """登录用户对自己的 metric 提交 feedback=1 → 200 + DB 写入。"""
    model_id = await _seed_model(db_session)
    metric_id = await _seed_metric(
        db_session, user_id=normal_user.id, model_id=model_id
    )
    await db_session.commit()

    body = {"metric_id": metric_id, "feedback": 1, "feedback_text": "great"}
    resp = await client.post(
        FEEDBACK_API, json=body, headers=auth_header(user_token)
    )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["success"] is True
    assert data["metric_id"] == metric_id

    # 验证 DB 真的写入了
    res = await db_session.execute(
        select(ABTestMetric).where(ABTestMetric.id == metric_id)
    )
    row = res.scalar_one()
    assert row.feedback == 1
    assert row.feedback_text == "great"


# ---------------------------------------------------------------------------
# 2) another user trying to give feedback → 403
# ---------------------------------------------------------------------------


async def test_other_user_cannot_feedback_others_metric(
    client, user_token, normal_user, admin_user, db_session
):
    """用户对别人的 metric 提交 feedback → 403。"""
    model_id = await _seed_model(db_session)
    # metric 属于 admin
    metric_id = await _seed_metric(
        db_session, user_id=admin_user.id, model_id=model_id
    )
    await db_session.commit()

    body = {"metric_id": metric_id, "feedback": 1}
    resp = await client.post(
        FEEDBACK_API, json=body, headers=auth_header(user_token)
    )

    assert resp.status_code == 403, resp.text


# ---------------------------------------------------------------------------
# 3) feedback_text is optional — None OK
# ---------------------------------------------------------------------------


async def test_feedback_text_optional(
    client, user_token, normal_user, db_session
):
    """feedback_text=None 也 OK（缺省 / None 都通过校验）。"""
    model_id = await _seed_model(db_session)
    metric_id = await _seed_metric(
        db_session, user_id=normal_user.id, model_id=model_id
    )
    await db_session.commit()

    body = {"metric_id": metric_id, "feedback": -1, "feedback_text": None}
    resp = await client.post(
        FEEDBACK_API, json=body, headers=auth_header(user_token)
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["success"] is True

    res = await db_session.execute(
        select(ABTestMetric).where(ABTestMetric.id == metric_id)
    )
    row = res.scalar_one()
    assert row.feedback == -1
    assert row.feedback_text is None


# ---------------------------------------------------------------------------
# 4) both user and admin tokens work — 主路径 200
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role_label", ["user", "admin"])
async def test_both_user_and_admin_tokens_can_feedback(
    client,
    user_token,
    admin_token,
    normal_user,
    admin_user,
    db_session,
    role_label,
):
    """user 和 admin 都能给 feedback（鉴权只要求已登录）。"""
    if role_label == "user":
        token = user_token
        owner = normal_user
    else:
        token = admin_token
        owner = admin_user

    model_id = await _seed_model(db_session)
    metric_id = await _seed_metric(
        db_session, user_id=owner.id, model_id=model_id
    )
    await db_session.commit()

    body = {"metric_id": metric_id, "feedback": 0}
    resp = await client.post(
        FEEDBACK_API, json=body, headers=auth_header(token)
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["success"] is True
