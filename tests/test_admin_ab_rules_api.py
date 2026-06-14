"""Phase 4 — Task 4.3: Admin A/B Rules CRUD API.

Behavior under test:
- All endpoints are admin-only (普通 user 拿到 403)。
- POST 创建一条 A/B 规则；strategy 必须是 ``user_hash_mod`` 或 ``random_weight``，
  target 必须是 ``chat`` 或 ``embedding``，否则 400。
- ``user_hash_mod`` 策略的 config 必须形如 ``{"mod": N, "mapping": {"0": "m1", ...}}``，
  其中 ``mapping`` 的所有 model_name 必须在 ``model_configs`` 表中存在且 enabled=True，
  否则 400。
- ``random_weight`` 策略的 config 必须形如 ``{"weights": {"m1": 0.7, ...}}``，
  weights 之和应接近 1.0（容差 0.01），否则 400。
- PATCH 支持部分更新；DELETE 返回 204。
"""

from __future__ import annotations

from sqlalchemy import select

from backend.models.model_config import ModelConfig
from backend.models.provider import ModelProvider
from tests.conftest import auth_header

API = "/api/v1/admin/ab-rules"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _make_provider(client, admin_token, *, name: str = "p_ab") -> int:
    """创建一个普通（不可删的）provider，返回 id。"""
    body = {
        "name": name,
        "display_name": "Provider For AB",
        "provider_type": "openai_compat",
        "api_base_url": "https://api.example.com/v1",
        "api_key": "sk-ab-1234",
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
    enabled: bool = True,
) -> int:
    """通过 admin API 创建一个 model，返回 id。"""
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
    mid = r.json()["id"]
    if not enabled:
        r2 = await client.patch(
            f"/api/v1/admin/models/{mid}",
            json={"enabled": False},
            headers=auth_header(admin_token),
        )
        assert r2.status_code == 200, r2.text
    return mid


def _user_hash_mod_config(*model_names: str) -> dict:
    """构造一个合法 user_hash_mod config（mod == len(model_names)）。"""
    mod = len(model_names)
    mapping = {str(i): name for i, name in enumerate(model_names)}
    return {"mod": mod, "mapping": mapping}


def _random_weight_config(*weights: tuple[str, float]) -> dict:
    """构造一个合法 random_weight config。"""
    return {"weights": {name: w for name, w in weights}}


# ---------------------------------------------------------------------------
# AuthZ
# ---------------------------------------------------------------------------


async def test_non_admin_get_ab_rules_returns_403(client, user_token):
    resp = await client.get(API, headers=auth_header(user_token))
    assert resp.status_code == 403


async def test_non_admin_post_ab_rules_returns_403(client, user_token):
    resp = await client.post(
        API,
        json={
            "name": "r_user",
            "enabled": True,
            "strategy": "user_hash_mod",
            "target": "chat",
            "config": _user_hash_mod_config("m1"),
        },
        headers=auth_header(user_token),
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Create — happy path
# ---------------------------------------------------------------------------


async def test_admin_create_user_hash_mod_rule_returns_201(
    client, admin_token
):
    pid = await _make_provider(client, admin_token, name="p_hash_mod")
    await _make_model(client, admin_token, pid, name="m1")
    await _make_model(client, admin_token, pid, name="m2")
    await _make_model(client, admin_token, pid, name="m3")

    body = {
        "name": "chat_hash_3",
        "enabled": True,
        "strategy": "user_hash_mod",
        "target": "chat",
        "config": _user_hash_mod_config("m1", "m2", "m3"),
        "description": "三选一 hash 分流",
    }
    resp = await client.post(API, json=body, headers=auth_header(admin_token))
    assert resp.status_code == 201, resp.text

    data = resp.json()
    assert data["name"] == "chat_hash_3"
    assert data["strategy"] == "user_hash_mod"
    assert data["target"] == "chat"
    assert data["enabled"] is True
    assert data["config"]["mod"] == 3
    assert data["config"]["mapping"]["0"] == "m1"
    assert data["description"] == "三选一 hash 分流"
    assert "id" in data
    assert "created_at" in data
    assert "updated_at" in data


async def test_admin_create_random_weight_rule_returns_201(
    client, admin_token
):
    pid = await _make_provider(client, admin_token, name="p_rand_w")
    await _make_model(client, admin_token, pid, name="m_a")
    await _make_model(client, admin_token, pid, name="m_b")

    body = {
        "name": "chat_rw",
        "enabled": True,
        "strategy": "random_weight",
        "target": "chat",
        "config": _random_weight_config(("m_a", 0.7), ("m_b", 0.3)),
    }
    resp = await client.post(API, json=body, headers=auth_header(admin_token))
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["strategy"] == "random_weight"
    assert abs(data["config"]["weights"]["m_a"] - 0.7) < 1e-9


# ---------------------------------------------------------------------------
# Create — validation failures
# ---------------------------------------------------------------------------


async def test_create_user_hash_mod_with_unknown_model_returns_400(
    client, admin_token
):
    pid = await _make_provider(client, admin_token, name="p_unknown")
    # 只创建 m1，但 mapping 引用了 m_unknown
    await _make_model(client, admin_token, pid, name="m1")

    body = {
        "name": "bad_mapping",
        "enabled": True,
        "strategy": "user_hash_mod",
        "target": "chat",
        "config": {"mod": 2, "mapping": {"0": "m1", "1": "m_unknown"}},
    }
    resp = await client.post(API, json=body, headers=auth_header(admin_token))
    assert resp.status_code == 400, resp.text
    assert "m_unknown" in resp.text or "not found" in resp.text.lower()


async def test_create_user_hash_mod_with_disabled_model_returns_400(
    client, admin_token
):
    pid = await _make_provider(client, admin_token, name="p_disabled")
    await _make_model(client, admin_token, pid, name="m1")
    await _make_model(client, admin_token, pid, name="m2", enabled=False)

    body = {
        "name": "bad_disabled",
        "enabled": True,
        "strategy": "user_hash_mod",
        "target": "chat",
        "config": {"mod": 2, "mapping": {"0": "m1", "1": "m2"}},
    }
    resp = await client.post(API, json=body, headers=auth_header(admin_token))
    assert resp.status_code == 400, resp.text


async def test_create_invalid_strategy_returns_400(client, admin_token):
    pid = await _make_provider(client, admin_token, name="p_bad_strategy")
    await _make_model(client, admin_token, pid, name="m1")
    body = {
        "name": "bad_strategy",
        "enabled": True,
        "strategy": "round_robin",  # 不在白名单
        "target": "chat",
        "config": _user_hash_mod_config("m1"),
    }
    resp = await client.post(API, json=body, headers=auth_header(admin_token))
    assert resp.status_code == 400, resp.text


async def test_create_invalid_target_returns_400(client, admin_token):
    pid = await _make_provider(client, admin_token, name="p_bad_target")
    await _make_model(client, admin_token, pid, name="m1")
    body = {
        "name": "bad_target",
        "enabled": True,
        "strategy": "user_hash_mod",
        "target": "image",  # 不在白名单
        "config": _user_hash_mod_config("m1"),
    }
    resp = await client.post(API, json=body, headers=auth_header(admin_token))
    assert resp.status_code == 400, resp.text


async def test_create_random_weight_sum_off_returns_400(client, admin_token):
    pid = await _make_provider(client, admin_token, name="p_bad_weights")
    await _make_model(client, admin_token, pid, name="m1")
    await _make_model(client, admin_token, pid, name="m2")

    body = {
        "name": "bad_weights",
        "enabled": True,
        "strategy": "random_weight",
        "target": "chat",
        "config": {"weights": {"m1": 0.5, "m2": 0.1}},  # sum=0.6, off
    }
    resp = await client.post(API, json=body, headers=auth_header(admin_token))
    assert resp.status_code == 400, resp.text


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


async def test_list_ab_rules_returns_array(client, admin_token):
    pid = await _make_provider(client, admin_token, name="p_list")
    await _make_model(client, admin_token, pid, name="m1")
    await _make_model(client, admin_token, pid, name="m2")

    body = {
        "name": "list_me",
        "enabled": True,
        "strategy": "user_hash_mod",
        "target": "chat",
        "config": {"mod": 2, "mapping": {"0": "m1", "1": "m2"}},
    }
    r = await client.post(API, json=body, headers=auth_header(admin_token))
    assert r.status_code == 201

    resp = await client.get(API, headers=auth_header(admin_token))
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert isinstance(data, list)
    names = {row["name"] for row in data}
    assert "list_me" in names


# ---------------------------------------------------------------------------
# Patch — enable / disable
# ---------------------------------------------------------------------------


async def test_patch_disable_rule_returns_200(client, admin_token):
    pid = await _make_provider(client, admin_token, name="p_patch")
    await _make_model(client, admin_token, pid, name="m1")
    await _make_model(client, admin_token, pid, name="m2")

    body = {
        "name": "patch_me",
        "enabled": True,
        "strategy": "user_hash_mod",
        "target": "chat",
        "config": {"mod": 2, "mapping": {"0": "m1", "1": "m2"}},
    }
    r = await client.post(API, json=body, headers=auth_header(admin_token))
    assert r.status_code == 201
    rule_id = r.json()["id"]

    resp = await client.patch(
        f"{API}/{rule_id}",
        json={"enabled": False},
        headers=auth_header(admin_token),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["enabled"] is False

    # 再启用回来
    resp2 = await client.patch(
        f"{API}/{rule_id}",
        json={"enabled": True},
        headers=auth_header(admin_token),
    )
    assert resp2.status_code == 200
    assert resp2.json()["enabled"] is True


async def test_patch_unknown_rule_returns_404(client, admin_token):
    resp = await client.patch(
        f"{API}/999999",
        json={"enabled": False},
        headers=auth_header(admin_token),
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


async def test_delete_rule_returns_204(client, admin_token, db_session):
    pid = await _make_provider(client, admin_token, name="p_del_rule")
    await _make_model(client, admin_token, pid, name="m1")

    body = {
        "name": "delete_me",
        "enabled": True,
        "strategy": "user_hash_mod",
        "target": "chat",
        "config": {"mod": 1, "mapping": {"0": "m1"}},
    }
    r = await client.post(API, json=body, headers=auth_header(admin_token))
    assert r.status_code == 201
    rule_id = r.json()["id"]

    resp = await client.delete(
        f"{API}/{rule_id}", headers=auth_header(admin_token)
    )
    assert resp.status_code == 204, resp.text

    # DB 中应已消失
    from backend.models.ab_test import ABTestRule
    row = await db_session.get(ABTestRule, rule_id)
    assert row is None


async def test_delete_unknown_rule_returns_404(client, admin_token):
    resp = await client.delete(
        f"{API}/999999", headers=auth_header(admin_token)
    )
    assert resp.status_code == 404
