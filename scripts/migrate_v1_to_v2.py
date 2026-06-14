"""Phase 5 — Task 5.2: v1 -> v2 in-process migration.

This is the lightweight, application-level bridge between the pre-Phase-5
schema (Phase 4 multi-model tables, but no built-in provider seeded) and a
fully-operational v2 install.

The migration itself is intentionally **data-only** — no DDL. Schema lives
in alembic (see ``200_phase5_seed_defaults``); this module's job is to fill
in the rows that every install should ship with:

    model_providers  +  minimax   (built-in, Fernet-encrypted api_key_enc)
    model_configs    +  MiniMax-M3 (chat, is_default_chat=True)
    model_configs    +  embo-01    (embedding, is_default_emb=True)

Idempotency:
- A v1 install is detected by the absence of a provider named ``"minimax"``.
- A v2 install already has ``minimax``; ``migrate()`` is a no-op.

The function is called from the FastAPI lifespan in ``backend/main.py`` at
every boot, so an operator never needs to remember to run it manually.
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.provider import ModelProvider
from scripts.seed import BUILTIN_PROVIDER_NAME, seed_default_models

logger = logging.getLogger(__name__)


# Public flag so callers (and tests) can inspect the install version.
async def _provider_exists(session: AsyncSession, name: str) -> bool:
    row = await session.execute(
        select(ModelProvider.id).where(ModelProvider.name == name)
    )
    return row.scalar_one_or_none() is not None


async def migrate(session: AsyncSession) -> dict[str, Any]:
    """Promote a v1 install to v2 by inserting the built-in defaults.

    Detection rule: ``ModelProvider.name == "minimax"`` is the v2 marker —
    a v1 install has none, a v2 install already has exactly one.

    Args:
        session: an open async SQLAlchemy session. The caller owns its
            lifecycle; this function only commits its own writes.

    Returns:
        A summary dict the caller can log:

        ``{"state": "v2", "action": "skipped", "provider_id": int}``
            when the built-in provider already exists (idempotent no-op).

        ``{"state": "v2", "action": "seeded", **seed_default_models(...)}``
            when v1 was detected and the seed actually ran.

        ``{"state": "v1", "action": "noop", ...}`` is **never** returned:
        this function guarantees the post-condition that the built-in
        ``minimax`` provider exists in the DB.
    """
    if await _provider_exists(session, BUILTIN_PROVIDER_NAME):
        existing_id = (
            await session.execute(
                select(ModelProvider.id).where(
                    ModelProvider.name == BUILTIN_PROVIDER_NAME
                )
            )
        ).scalar_one()
        logger.info(
            "v1->v2 migrate: skipped (built-in %r provider already exists, id=%s)",
            BUILTIN_PROVIDER_NAME,
            existing_id,
        )
        return {
            "state": "v2",
            "action": "skipped",
            "provider_id": existing_id,
        }

    logger.info(
        "v1->v2 migrate: detected v1 install (no %r provider), seeding defaults",
        BUILTIN_PROVIDER_NAME,
    )
    summary = await seed_default_models(session)
    summary = {"state": "v2", "action": "seeded", **summary}
    logger.info("v1->v2 migrate: seeded ok — %s", summary)
    return summary


__all__ = ["migrate"]


# Convenience synchronous entry point for scripts: ``python -m scripts.migrate_v1_to_v2``
async def _amain() -> None:
    from backend import database
    from backend.config import settings

    await database.init_db(settings.DATABASE_URL)
    engine = database.get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(database.Base.metadata.create_all)
    async with database.AsyncSessionLocal() as session:
        summary = await migrate(session)
        print(f"migrate result: {summary}")


if __name__ == "__main__":
    import asyncio

    asyncio.run(_amain())