"""Phase 5 — Task 5.1: seed_default_models.

Tests target the real PostgreSQL instance configured in ``.env`` via the
shared ``db_session`` fixture from ``tests/conftest.py``. The fixture:

- initialises the async engine once per session
- runs ``Base.metadata.create_all`` so all 4 multi-model tables exist
- truncates those 4 tables before each test for full isolation

We deliberately avoid SQLite here — the partial unique indexes that guard
``is_default_chat`` / ``is_default_emb`` use ``postgresql_where`` clauses,
so the real DB is the only honest substrate.
"""
from __future__ import annotations

import os

# Ensure a deterministic Fernet key for any crypto path that runs.
os.environ.setdefault(
    "ENCRYPTION_KEY", "P1bpcHvVWWQ696WirBRFSyTPbdyeQGfv3-cNiM_-bEw"
)

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.crypto import decrypt_key
from backend.models.model_config import ModelConfig
from backend.models.provider import ModelProvider
from scripts.seed import (
    BUILTIN_PROVIDER_NAME,
    BUILTIN_PROVIDER_TYPE,
    BUILTIN_PROVIDER_BASE_URL,
    DEFAULT_CHAT_MODEL_NAME,
    DEFAULT_EMB_MODEL_NAME,
    seed_default_models,
)


# ---------------------------------------------------------------------------
# 1. Built-in provider is created with encrypted key
# ---------------------------------------------------------------------------


async def test_seed_creates_minimax_provider_with_encrypted_key(
    db_session: AsyncSession,
    monkeypatch,
):
    """First call inserts a provider with is_builtin=True and Fernet-encrypted
    api_key_enc; the encrypted bytes round-trip back to the original plaintext."""
    # Ensure settings sees a real key (in case .env didn't export one to the
    # test subprocess). The key only needs to be non-empty for encrypt_key().
    from backend import config as cfg

    monkeypatch.setattr(cfg.settings, "MINIMAX_API_KEY", "test-key-1234567890", raising=False)
    monkeypatch.setattr(cfg.settings, "MINIMAX_CN_API_KEY", "", raising=False)

    result = await seed_default_models(db_session)

    assert result["provider_created"] is True
    assert isinstance(result["provider_id"], int) and result["provider_id"] > 0

    # Reload from DB to make sure the row actually persisted
    provider = (
        await db_session.execute(
            select(ModelProvider).where(ModelProvider.name == BUILTIN_PROVIDER_NAME)
        )
    ).scalar_one()

    assert provider.is_builtin is True
    assert provider.enabled is True
    assert provider.provider_type == BUILTIN_PROVIDER_TYPE
    assert provider.api_base_url == BUILTIN_PROVIDER_BASE_URL
    # Encrypted blob must NOT equal the plaintext, and must decrypt cleanly
    assert provider.api_key_enc != b"test-key-1234567890"
    assert decrypt_key(provider.api_key_enc) == "test-key-1234567890"


# ---------------------------------------------------------------------------
# 2. Two default models are inserted with correct capabilities
# ---------------------------------------------------------------------------


async def test_seed_creates_chat_and_embedding_models(
    db_session: AsyncSession,
    monkeypatch,
):
    """After seeding, MiniMax-M3 is the default chat model and embo-01 is the
    default embedding model. Both are linked to the minimax provider and have
    the right context windows."""
    from backend import config as cfg

    monkeypatch.setattr(cfg.settings, "MINIMAX_API_KEY", "another-test-key", raising=False)
    monkeypatch.setattr(cfg.settings, "MINIMAX_CN_API_KEY", "", raising=False)

    result = await seed_default_models(db_session)

    chat_row = (
        await db_session.execute(
            select(ModelConfig).where(ModelConfig.id == result["chat_id"])
        )
    ).scalar_one()
    emb_row = (
        await db_session.execute(
            select(ModelConfig).where(ModelConfig.id == result["emb_id"])
        )
    ).scalar_one()

    # Chat row
    assert chat_row.model_name == DEFAULT_CHAT_MODEL_NAME
    assert chat_row.model_type == "chat"
    assert chat_row.is_default_chat is True
    assert chat_row.is_default_emb is False
    assert chat_row.context_window == 200000
    assert chat_row.enabled is True

    # Embedding row
    assert emb_row.model_name == DEFAULT_EMB_MODEL_NAME
    assert emb_row.model_type == "embedding"
    assert emb_row.is_default_emb is True
    assert emb_row.is_default_chat is False
    assert emb_row.context_window == 8192
    assert emb_row.enabled is True

    # Both must point at the same provider
    assert chat_row.provider_id == emb_row.provider_id == result["provider_id"]

    # Only one chat default exists in the entire table
    chat_defaults = (
        await db_session.execute(
            select(ModelConfig).where(ModelConfig.is_default_chat.is_(True))
        )
    ).scalars().all()
    assert len(chat_defaults) == 1
    assert chat_defaults[0].id == chat_row.id

    emb_defaults = (
        await db_session.execute(
            select(ModelConfig).where(ModelConfig.is_default_emb.is_(True))
        )
    ).scalars().all()
    assert len(emb_defaults) == 1
    assert emb_defaults[0].id == emb_row.id


# ---------------------------------------------------------------------------
# 3. Idempotent — second call leaves counts and ids intact
# ---------------------------------------------------------------------------


async def test_seed_is_idempotent_on_repeat_calls(
    db_session: AsyncSession,
    monkeypatch,
):
    """Calling seed_default_models twice must NOT create duplicates. The
    returned ids from the second call must equal the ones from the first."""
    from backend import config as cfg

    monkeypatch.setattr(cfg.settings, "MINIMAX_API_KEY", "idempotent-key", raising=False)
    monkeypatch.setattr(cfg.settings, "MINIMAX_CN_API_KEY", "", raising=False)

    first = await seed_default_models(db_session)
    second = await seed_default_models(db_session)

    # Second call should report "nothing new"
    assert second["provider_created"] is False
    assert second["chat_created"] is False
    assert second["emb_created"] is False

    # IDs are stable across calls
    assert second["provider_id"] == first["provider_id"]
    assert second["chat_id"] == first["chat_id"]
    assert second["emb_id"] == first["emb_id"]

    # Tables each hold exactly one provider and two models
    providers = (
        await db_session.execute(select(ModelProvider))
    ).scalars().all()
    assert len(providers) == 1

    models = (
        await db_session.execute(select(ModelConfig))
    ).scalars().all()
    assert len(models) == 2


# ---------------------------------------------------------------------------
# 4. Empty / missing MINIMAX_API_KEY still seeds (placeholder ciphertext)
# ---------------------------------------------------------------------------


async def test_seed_works_without_api_key_env(
    db_session: AsyncSession,
    monkeypatch,
):
    """If the operator has not set MINIMAX_API_KEY yet, the seed must still
    complete (with a placeholder) so the rest of the system can boot. A
    warning is acceptable; an exception is not."""
    from backend import config as cfg

    monkeypatch.setattr(cfg.settings, "MINIMAX_API_KEY", "", raising=False)
    monkeypatch.setattr(cfg.settings, "MINIMAX_CN_API_KEY", "", raising=False)

    result = await seed_default_models(db_session)
    assert result["provider_created"] is True
    assert result["chat_created"] is True
    assert result["emb_created"] is True

    provider = (
        await db_session.execute(
            select(ModelProvider).where(ModelProvider.name == BUILTIN_PROVIDER_NAME)
        )
    ).scalar_one()
    # Placeholder still decrypts to something non-empty
    assert decrypt_key(provider.api_key_enc) != ""