"""Phase 4 — Task 4.1: Admin Provider CRUD API.

Uses the real FastAPI app (ASGI in-process) + real PG.
Asserts the encrypted-at-rest / never-returned-plaintext contract and
the built-in / FK-delete guards.
"""

from __future__ import annotations

from sqlalchemy import select

from backend.core.crypto import decrypt_key
from backend.models.provider import ModelProvider
from tests.conftest import auth_header

API = "/api/v1/admin/providers"


# ---------------------------------------------------------------------------
# AuthZ
# ---------------------------------------------------------------------------


async def test_non_admin_get_providers_returns_403(client, user_token):
    resp = await client.get(API, headers=auth_header(user_token))
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


async def test_admin_create_provider_returns_201_with_key_last_4(
    client, admin_token
):
    body = {
        "name": "openai_test",
        "display_name": "OpenAI (test)",
        "provider_type": "openai_compat",
        "api_base_url": "https://api.example.com/v1",
        "api_key": "sk-abcdefghijklmnop1234",
        "extra_config": {"timeout": 30},
    }
    resp = await client.post(API, json=body, headers=auth_header(admin_token))
    assert resp.status_code == 201, resp.text

    data = resp.json()
    assert data["name"] == "openai_test"
    assert data["display_name"] == "OpenAI (test)"
    assert data["provider_type"] == "openai_compat"
    assert data["api_base_url"] == "https://api.example.com/v1"
    assert data["key_last_4"] == "1234"
    assert data["enabled"] is True
    assert data["is_builtin"] is False
    assert data["extra_config"] == {"timeout": 30}

    # The plaintext API key MUST NOT be present anywhere in the response.
    raw_text = resp.text
    assert "sk-abcdefghijklmnop1234" not in raw_text
    assert "api_key" not in data  # no plaintext key field, no enc blob field
    assert "api_key_enc" not in data


async def test_create_provider_duplicate_name_returns_400(
    client, admin_token
):
    body = {
        "name": "dup_prov",
        "display_name": "First",
        "provider_type": "openai_compat",
        "api_key": "sk-1234",
    }
    r1 = await client.post(API, json=body, headers=auth_header(admin_token))
    assert r1.status_code == 201, r1.text

    body2 = {
        "name": "dup_prov",
        "display_name": "Second",
        "provider_type": "openai_compat",
        "api_key": "sk-5678",
    }
    r2 = await client.post(API, json=body2, headers=auth_header(admin_token))
    assert r2.status_code == 400, r2.text


async def test_create_provider_persists_fernet_encrypted_key(
    client, admin_token, db_session
):
    plaintext = "sk-enc-checks-secret-9999"
    body = {
        "name": "enc_check",
        "display_name": "Enc Check",
        "provider_type": "openai_compat",
        "api_key": plaintext,
    }
    resp = await client.post(API, json=body, headers=auth_header(admin_token))
    assert resp.status_code == 201

    row = (
        await db_session.execute(
            select(ModelProvider).where(ModelProvider.name == "enc_check")
        )
    ).scalar_one()
    # The DB blob is bytes, NOT equal to the plaintext
    assert row.api_key_enc != plaintext.encode()
    # And it round-trips via decrypt_key
    assert decrypt_key(row.api_key_enc) == plaintext


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


async def test_list_providers_includes_just_created(client, admin_token):
    body = {
        "name": "listme",
        "display_name": "List Me",
        "provider_type": "openai_compat",
        "api_key": "sk-listme-0000",
    }
    create = await client.post(API, json=body, headers=auth_header(admin_token))
    assert create.status_code == 201

    resp = await client.get(API, headers=auth_header(admin_token))
    assert resp.status_code == 200
    items = resp.json()
    names = {p["name"] for p in items}
    assert "listme" in names
    # No item exposes the raw enc blob
    for p in items:
        assert "api_key_enc" not in p
        assert "api_key" not in p


# ---------------------------------------------------------------------------
# Patch
# ---------------------------------------------------------------------------


async def test_patch_provider_updates_fields_and_rotates_key(
    client, admin_token, db_session
):
    body = {
        "name": "patchme",
        "display_name": "Old",
        "provider_type": "openai_compat",
        "api_key": "sk-old-1234",
    }
    create = await client.post(API, json=body, headers=auth_header(admin_token))
    pid = create.json()["id"]

    patch_body = {
        "display_name": "New",
        "api_key": "sk-new-5678",
        "enabled": False,
    }
    resp = await client.patch(
        f"{API}/{pid}", json=patch_body, headers=auth_header(admin_token)
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["display_name"] == "New"
    assert data["enabled"] is False
    assert data["key_last_4"] == "5678"
    assert "sk-new-5678" not in resp.text

    # And the new key actually round-trips in the DB
    row = await db_session.get(ModelProvider, pid)
    assert decrypt_key(row.api_key_enc) == "sk-new-5678"


# ---------------------------------------------------------------------------
# Delete — guards
# ---------------------------------------------------------------------------


async def test_delete_builtin_provider_returns_400(
    client, admin_token, db_session
):
    p = ModelProvider(
        name="builtin_one",
        display_name="Built-in",
        provider_type="minimax",
        api_key_enc=b"\x00" * 4,  # placeholder; we never decrypt
        is_builtin=True,
    )
    db_session.add(p)
    await db_session.commit()
    await db_session.refresh(p)

    resp = await client.delete(
        f"{API}/{p.id}", headers=auth_header(admin_token)
    )
    assert resp.status_code == 400, resp.text
    assert "built-in" in resp.json()["detail"].lower()


async def test_delete_provider_referenced_by_model_returns_409(
    client, admin_token, db_session
):
    # Create provider via API
    body = {
        "name": "with_models",
        "display_name": "With Models",
        "provider_type": "openai_compat",
        "api_key": "sk-xxxx",
    }
    create = await client.post(API, json=body, headers=auth_header(admin_token))
    pid = create.json()["id"]

    # Manually insert a ModelConfig that points at it
    from backend.models.model_config import ModelConfig

    db_session.add(
        ModelConfig(
            provider_id=pid,
            model_name="some-model",
            display_name="Some Model",
            model_type="chat",
        )
    )
    await db_session.commit()

    resp = await client.delete(
        f"{API}/{pid}", headers=auth_header(admin_token)
    )
    assert resp.status_code == 409, resp.text
    assert "model" in resp.json()["detail"].lower()


async def test_delete_provider_unreferenced_returns_204(
    client, admin_token
):
    body = {
        "name": "deletable",
        "display_name": "Deletable",
        "provider_type": "openai_compat",
        "api_key": "sk-del-0000",
    }
    create = await client.post(API, json=body, headers=auth_header(admin_token))
    pid = create.json()["id"]

    resp = await client.delete(
        f"{API}/{pid}", headers=auth_header(admin_token)
    )
    assert resp.status_code == 204
