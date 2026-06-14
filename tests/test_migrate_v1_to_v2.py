"""Phase 5 — Task 5.2: migrate_v1_to_v2.

Targets the real PostgreSQL backend via the shared ``db_session`` fixture
(``tests/conftest.py``). Each test starts from a clean slate because
``_cleanup_phase4_tables`` autouse-fixture truncates all 4 multi-model
tables before every test.
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

from backend.models.model_config import ModelConfig
from backend.models.provider import ModelProvider
from scripts.migrate_v1_to_v2 import migrate
from scripts.seed import (
    BUILTIN_PROVIDER_NAME,
    DEFAULT_CHAT_MODEL_NAME,
    DEFAULT_EMB_MODEL_NAME,
)


# ---------------------------------------------------------------------------
# 1. v1 (empty tables) -> v2 migration inserts the built-in provider & 2 models
# ---------------------------------------------------------------------------


async def test_migrate_from_empty_tables_seeds_defaults(
    db_session: AsyncSession,
    monkeypatch,
):
    """Starting with zero providers and zero models, calling migrate() must
    create exactly the built-in minimax provider plus the two default models
    (chat + embedding), and report ``action='seeded'``."""
    from backend import config as cfg

    monkeypatch.setattr(cfg.settings, "MINIMAX_API_KEY", "migrate-test-key", raising=False)
    monkeypatch.setattr(cfg.settings, "MINIMAX_CN_API_KEY", "", raising=False)

    # Sanity: tables really are empty
    assert (await db_session.execute(select(ModelProvider))).scalars().all() == []
    assert (await db_session.execute(select(ModelConfig))).scalars().all() == []

    summary = await migrate(db_session)

    # Summary contract
    assert summary["state"] == "v2"
    assert summary["action"] == "seeded"
    assert summary["provider_created"] is True
    assert summary["chat_created"] is True
    assert summary["emb_created"] is True

    # Provider exists with the right flags
    provider = (
        await db_session.execute(
            select(ModelProvider).where(ModelProvider.name == BUILTIN_PROVIDER_NAME)
        )
    ).scalar_one()
    assert provider.is_builtin is True
    assert provider.enabled is True

    # Two models exist, both linked to the built-in provider
    models = (
        await db_session.execute(
            select(ModelConfig).where(ModelConfig.provider_id == provider.id)
        )
    ).scalars().all()
    by_name = {m.model_name: m for m in models}
    assert set(by_name) == {DEFAULT_CHAT_MODEL_NAME, DEFAULT_EMB_MODEL_NAME}

    chat = by_name[DEFAULT_CHAT_MODEL_NAME]
    assert chat.model_type == "chat"
    assert chat.is_default_chat is True
    assert chat.is_default_emb is False

    emb = by_name[DEFAULT_EMB_MODEL_NAME]
    assert emb.model_type == "embedding"
    assert emb.is_default_emb is True
    assert emb.is_default_chat is False


# ---------------------------------------------------------------------------
# 2. v2 install — calling migrate() again is a no-op (idempotent)
# ---------------------------------------------------------------------------


async def test_migrate_skips_when_already_seeded(
    db_session: AsyncSession,
    monkeypatch,
):
    """If the built-in minimax provider already exists, migrate() must
    return ``action='skipped'`` and MUST NOT create duplicate rows."""
    from backend import config as cfg
    from scripts.seed import seed_default_models

    monkeypatch.setattr(cfg.settings, "MINIMAX_API_KEY", "already-seeded-key", raising=False)
    monkeypatch.setattr(cfg.settings, "MINIMAX_CN_API_KEY", "", raising=False)

    # First migration: v1 -> v2
    first = await migrate(db_session)
    assert first["action"] == "seeded"

    # Snapshot the row count and IDs before the second call
    providers_before = (
        await db_session.execute(select(ModelProvider))
    ).scalars().all()
    models_before = (
        await db_session.execute(select(ModelConfig))
    ).scalars().all()
    assert len(providers_before) == 1
    assert len(models_before) == 2

    # Second migration: must be a no-op
    second = await migrate(db_session)
    assert second["state"] == "v2"
    assert second["action"] == "skipped"
    # ``provider_id`` is echoed back so callers can wire it up
    assert second["provider_id"] == first["provider_id"]

    # No duplicates created
    providers_after = (
        await db_session.execute(select(ModelProvider))
    ).scalars().all()
    models_after = (
        await db_session.execute(select(ModelConfig))
    ).scalars().all()
    assert len(providers_after) == len(providers_before) == 1
    assert len(models_after) == len(models_before) == 2

    # Make sure no seed_default_models was called (its *created flags
    # should not appear on a skipped migration)
    assert "provider_created" not in second


# ---------------------------------------------------------------------------
# 3. Repeated calls converge — three migrate() invocations still leave the
#    schema in the v2 shape with no duplicate rows.
# ---------------------------------------------------------------------------


async def test_migrate_three_consecutive_calls_keep_table_count_stable(
    db_session: AsyncSession,
    monkeypatch,
):
    """Hammering migrate() three times in a row must keep the row count
    stable: 1 provider, 2 models. Useful regression guard against any
    accidental fan-out in the seeding helper."""
    from backend import config as cfg

    monkeypatch.setattr(cfg.settings, "MINIMAX_API_KEY", "stable-key", raising=False)
    monkeypatch.setattr(cfg.settings, "MINIMAX_CN_API_KEY", "", raising=False)

    r1 = await migrate(db_session)
    r2 = await migrate(db_session)
    r3 = await migrate(db_session)

    # First call seeded, the rest skipped
    assert r1["action"] == "seeded"
    assert r2["action"] == "skipped"
    assert r3["action"] == "skipped"

    # Same provider id echoed across all three calls
    assert r1["provider_id"] == r2["provider_id"] == r3["provider_id"]

    # Final row counts: 1 provider + 2 models
    providers = (
        await db_session.execute(select(ModelProvider))
    ).scalars().all()
    models = (await db_session.execute(select(ModelConfig))).scalars().all()
    assert len(providers) == 1
    assert len(models) == 2