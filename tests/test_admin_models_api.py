"""Phase 4 — Task 4.2: Admin ModelConfig CRUD API.

Behavior under test:
- POST creates a model row linked to an existing provider.
- PATCH ``is_default_chat=True`` swaps the global default (only one
  row may carry True at a time).
- PATCH ``is_default_emb=True`` returns a ``warning`` field if a
  different model was previously the embedding default.
- model_type is restricted to ``chat`` / ``embedding``; anything else
  returns 400 (or 422 — FastAPI validation).
- DELETE removes the row when there are no FK references.
"""

from __future__ import annotations

from sqlalchemy import select

from backend.models.model_config import ModelConfig
from backend.models.provider import ModelProvider
from tests.conftest import auth_header

API = "/api/v1/admin/models"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _make_provider(client, admin_token, *, name="p_models") -> int:
    body = {
        "name": name,
        "display_name": "Provider For Models",
        "provider_type": "openai_compat",
        "api_key": "sk-prov-1234",
    }
    r = await client.post(
        "/api/v1/admin/providers", json=body, headers=auth_header(admin_token)
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _create_body(provider_id: int, **overrides) -> dict:
    body = {
        "provider_id": provider_id,
        "model_name": "m-default",
        "display_name": "Default Model",
        "model_type": "chat",
        "context_window": 128000,
        "is_default_chat": False,
        "is_default_emb": False,
        "extra_config": {},
    }
    body.update(overrides)
    return body


# ---------------------------------------------------------------------------
# AuthZ
# ---------------------------------------------------------------------------


async def test_non_admin_get_models_returns_403(client, user_token):
    resp = await client.get(API, headers=auth_header(user_token))
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


async def test_admin_create_model_returns_201(client, admin_token):
    pid = await _make_provider(client, admin_token)
    body = _create_body(pid, model_name="gpt-4o", is_default_chat=True)
    resp = await client.post(API, json=body, headers=auth_header(admin_token))
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["model_name"] == "gpt-4o"
    assert data["model_type"] == "chat"
    assert data["is_default_chat"] is True
    assert data["is_default_emb"] is False
    assert data["context_window"] == 128000


async def test_create_model_invalid_type_returns_4xx(client, admin_token):
    pid = await _make_provider(client, admin_token, name="p_inv")
    body = _create_body(pid, model_name="bad", model_type="bogus_type")
    resp = await client.post(API, json=body, headers=auth_header(admin_token))
    assert 400 <= resp.status_code < 500, resp.text


async def test_create_model_missing_provider_returns_4xx(
    client, admin_token
):
    body = _create_body(999999, model_name="orphan")
    resp = await client.post(API, json=body, headers=auth_header(admin_token))
    assert 400 <= resp.status_code < 500, resp.text


# ---------------------------------------------------------------------------
# Patch — is_default_chat swap
# ---------------------------------------------------------------------------


async def test_patch_is_default_chat_swaps_global_default(
    client, admin_token, db_session
):
    pid = await _make_provider(client, admin_token, name="p_swap")
    # Two chat models — first is default
    r1 = await client.post(
        API,
        json=_create_body(pid, model_name="m1", is_default_chat=True),
        headers=auth_header(admin_token),
    )
    assert r1.status_code == 201
    m1_id = r1.json()["id"]
    r2 = await client.post(
        API,
        json=_create_body(pid, model_name="m2", is_default_chat=False),
        headers=auth_header(admin_token),
    )
    assert r2.status_code == 201
    m2_id = r2.json()["id"]

    # Swap: promote m2
    resp = await client.patch(
        f"{API}/{m2_id}",
        json={"is_default_chat": True},
        headers=auth_header(admin_token),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_default_chat"] is True

    # DB invariant: exactly one default, and it's m2
    rows = (
        await db_session.execute(
            select(ModelConfig).where(
                ModelConfig.model_type == "chat",
                ModelConfig.is_default_chat.is_(True),
            )
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].id == m2_id
    # And m1 should be False
    m1 = await db_session.get(ModelConfig, m1_id)
    assert m1.is_default_chat is False


# ---------------------------------------------------------------------------
# Patch — is_default_emb warning
# ---------------------------------------------------------------------------


async def test_patch_is_default_emb_to_different_model_returns_warning(
    client, admin_token
):
    pid = await _make_provider(client, admin_token, name="p_emb")
    r1 = await client.post(
        API,
        json=_create_body(
            pid, model_name="emb-old", model_type="embedding",
            is_default_emb=True,
        ),
        headers=auth_header(admin_token),
    )
    assert r1.status_code == 201
    old_id = r1.json()["id"]
    r2 = await client.post(
        API,
        json=_create_body(
            pid, model_name="emb-new", model_type="embedding",
            is_default_emb=False,
        ),
        headers=auth_header(admin_token),
    )
    assert r2.status_code == 201
    new_id = r2.json()["id"]

    resp = await client.patch(
        f"{API}/{new_id}",
        json={"is_default_emb": True},
        headers=auth_header(admin_token),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["is_default_emb"] is True
    # Warning should mention vector-store / rebuild
    assert "warning" in data
    assert "向量" in data["warning"] or "vector" in data["warning"].lower()


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


async def test_delete_model_returns_204(client, admin_token):
    pid = await _make_provider(client, admin_token, name="p_del")
    r = await client.post(
        API,
        json=_create_body(pid, model_name="deleteme"),
        headers=auth_header(admin_token),
    )
    mid = r.json()["id"]
    resp = await client.delete(
        f"{API}/{mid}", headers=auth_header(admin_token)
    )
    assert resp.status_code == 204, resp.text
