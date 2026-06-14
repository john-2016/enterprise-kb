"""Seed test data.

This module is split into two responsibilities:

1. ``main()`` — legacy: create the bootstrap users (admin / editor / viewer)
   used for manual smoke testing. Safe to run multiple times because
   ``register_user`` is idempotent.

2. ``seed_default_models(session)`` — **idempotent** seeding of the system-
   level built-in ``minimax`` provider and its two default models
   (``MiniMax-M3`` for chat, ``embo-01`` for embeddings). Called by:

   - ``scripts/migrate_v1_to_v2.migrate`` when a v1 install is detected
   - the FastAPI lifespan startup hook in ``backend/main.py`` (auto-migrate)

   The function is safe to call repeatedly: existing rows are detected by
   their unique business keys (``ModelProvider.name``, ``ModelConfig.provider_id
   + model_name``) and skipped.

Encryption:
- ``MINIMAX_API_KEY`` is encrypted with Fernet via ``backend.core.crypto.encrypt_key``
  and stored as ``ModelProvider.api_key_enc`` bytes. Plaintext is **never**
  persisted.
"""
import asyncio
import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.core.crypto import encrypt_key
from backend import database
from backend.models import User
from backend.models.model_config import ModelConfig
from backend.models.provider import ModelProvider
from backend.services.auth_service import register_user

logger = logging.getLogger(__name__)


# Built-in provider / model constants -----------------------------------------
BUILTIN_PROVIDER_NAME = "minimax"
BUILTIN_PROVIDER_TYPE = "minimax"
BUILTIN_PROVIDER_BASE_URL = "https://api.minimaxi.com/v1"
BUILTIN_PROVIDER_DISPLAY_NAME = "MiniMax (built-in)"

DEFAULT_CHAT_MODEL_NAME = "MiniMax-M3"
DEFAULT_CHAT_MODEL_DISPLAY = "MiniMax-M3 Chat"
DEFAULT_CHAT_CONTEXT_WINDOW = 200000

DEFAULT_EMB_MODEL_NAME = "embo-01"
DEFAULT_EMB_MODEL_DISPLAY = "embo-01 Embeddings"
DEFAULT_EMB_CONTEXT_WINDOW = 8192


async def bootstrap_admin(session: AsyncSession) -> None:
    """Create bootstrap users on a fresh install.

    Idempotent: if the admin user already exists, nothing is done. On a fresh
    install, the admin password is randomly generated and persisted to
    ``data/.admin_password`` so install.sh can read it back and print it.

    Called by:
      - ``main()`` (manual ``python -m scripts.seed``)
      - the FastAPI lifespan startup hook in ``backend/main.py`` (auto-run
        on every container start, so fresh ``./install.sh`` always works
        without any extra operator step).
    """
    existing_admin = await _user_exists(session, "admin")
    if existing_admin:
        return

    import secrets as _secrets
    admin_pwd = _secrets.token_urlsafe(16)
    _write_admin_password_file(admin_pwd)
    print("=" * 60)
    print("✓ First-time install detected.")
    print("  Admin user:  admin")
    print(f"  Random pass: {admin_pwd}")
    print("  (Saved to: data/.admin_password, chmod 600)")
    print("  ⚠️  Log in and change this password immediately.")
    print("=" * 60)
    await register_user(
        session, "admin", "admin@example.com", admin_pwd, role="admin"
    )

    # Editor / viewer: keep simple defaults for dev convenience
    editor = await register_user(
        session, "editor", "editor@example.com", "editor123", role="editor"
    )
    print(f"✓ Editor user created: {editor['username']} / editor123")
    viewer = await register_user(
        session, "viewer", "viewer@example.com", "viewer123", role="viewer"
    )
    print(f"✓ Viewer user created: {viewer['username']} / viewer123")


async def main() -> None:
    """Legacy entry — bootstrap users for manual smoke testing.

    On a fresh install (no admin user), the admin password is generated
    with ``secrets.token_urlsafe`` and written to ``data/.admin_password``
    (chmod 600) so the operator can read it back. The path is printed to
    stdout with a clear warning to change the password after first login.
    """
    await database.init_db(settings.DATABASE_URL)
    engine = database.get_engine()
    async with database.AsyncSessionLocal() as session:
        existing_admin = await _user_exists(session, "admin")
        if existing_admin:
            print("✓ Admin user already exists — leaving passwords unchanged.")
        else:
            await bootstrap_admin(session)
        await session.commit()


async def _user_exists(session: AsyncSession, username: str) -> bool:
    """Return True if a user with the given username already exists."""
    from backend.models import User
    result = await session.execute(
        select(User).where(User.username == username)
    )
    return result.scalar_one_or_none() is not None


def _write_admin_password_file(password: str) -> None:
    """Persist the random admin password so install.sh can read it back.

    File is chmod 600 (owner read/write only) and lives in ``data/`` which
    is git-ignored. On a fresh checkout, ``install.sh`` reads this file and
    prints the password to the operator.
    """
    import os
    from pathlib import Path
    pw_file = Path("data/.admin_password")
    pw_file.parent.mkdir(parents=True, exist_ok=True)
    pw_file.write_text(password + "\n")
    os.chmod(pw_file, 0o600)


def _resolve_minimax_api_key() -> Optional[str]:
    """Return the operator-configured MiniMax API key (may be empty)."""
    return settings.MINIMAX_API_KEY or None


async def seed_default_models(session: AsyncSession) -> dict[str, int | bool]:
    """Idempotently insert the built-in ``minimax`` provider and its defaults.

    Returns a small summary dict the caller can log/inspect:
        {
            "provider_created": bool,
            "chat_created": bool,
            "emb_created": bool,
            "provider_id": int (if provider now exists),
            "chat_id": int (if chat model now exists),
            "emb_id": int (if embedding model now exists),
        }

    Behaviour:
    - Provider: detected by ``ModelProvider.name == "minimax"``. If absent,
      it is created with ``is_builtin=True`` and the API key encrypted via
      Fernet. If present and already a built-in, left untouched.
    - Chat model: ``MiniMax-M3`` under the ``minimax`` provider, marked
      ``is_default_chat=True`` and ``model_type="chat"``. If another row
      already carries the chat default, it is cleared first so the partial
      unique index stays consistent.
    - Embedding model: ``embo-01`` under ``minimax``, marked
      ``is_default_emb=True`` and ``model_type="embedding"``. Same swap
      behaviour as above.
    """
    # ---- 1. Provider -----------------------------------------------------
    result = await session.execute(
        select(ModelProvider).where(ModelProvider.name == BUILTIN_PROVIDER_NAME)
    )
    provider = result.scalar_one_or_none()
    provider_created = False
    if provider is None:
        api_key = _resolve_minimax_api_key()
        # encrypt_key requires a non-empty plaintext; fall back to a sentinel
        # so dev installs without a real key still seed. The sentinel is
        # obviously unusable for live calls but unblocks routing tests.
        if not api_key:
            api_key = "unconfigured-minimax-key"
            logger.warning(
                "MINIMAX_API_KEY is empty — seeding provider with placeholder; "
                "real traffic will fail until the operator updates it."
            )
        provider = ModelProvider(
            name=BUILTIN_PROVIDER_NAME,
            display_name=BUILTIN_PROVIDER_DISPLAY_NAME,
            provider_type=BUILTIN_PROVIDER_TYPE,
            api_base_url=BUILTIN_PROVIDER_BASE_URL,
            api_key_enc=encrypt_key(api_key),
            extra_config={},
            enabled=True,
            is_builtin=True,
        )
        session.add(provider)
        await session.flush()  # populate provider.id
        provider_created = True
    else:
        # Promote legacy / manually-created provider to built-in so subsequent
        # guards (FK delete, migrations) treat it correctly.
        if not provider.is_builtin:
            provider.is_builtin = True

    provider_id = provider.id

    # ---- 2. Chat default -------------------------------------------------
    chat_created = await _ensure_default_model(
        session,
        provider_id=provider_id,
        model_name=DEFAULT_CHAT_MODEL_NAME,
        display_name=DEFAULT_CHAT_MODEL_DISPLAY,
        model_type="chat",
        context_window=DEFAULT_CHAT_CONTEXT_WINDOW,
        is_default_chat=True,
        is_default_emb=False,
    )

    # ---- 3. Embedding default -------------------------------------------
    emb_created = await _ensure_default_model(
        session,
        provider_id=provider_id,
        model_name=DEFAULT_EMB_MODEL_NAME,
        display_name=DEFAULT_EMB_MODEL_DISPLAY,
        model_type="embedding",
        context_window=DEFAULT_EMB_CONTEXT_WINDOW,
        is_default_chat=False,
        is_default_emb=True,
    )

    await session.commit()

    # Resolve final ids (may have existed already)
    chat_row = (
        await session.execute(
            select(ModelConfig).where(
                ModelConfig.provider_id == provider_id,
                ModelConfig.model_name == DEFAULT_CHAT_MODEL_NAME,
            )
        )
    ).scalar_one()
    emb_row = (
        await session.execute(
            select(ModelConfig).where(
                ModelConfig.provider_id == provider_id,
                ModelConfig.model_name == DEFAULT_EMB_MODEL_NAME,
            )
        )
    ).scalar_one()

    return {
        "provider_created": provider_created,
        "chat_created": chat_created,
        "emb_created": emb_created,
        "provider_id": provider_id,
        "chat_id": chat_row.id,
        "emb_id": emb_row.id,
    }


async def _ensure_default_model(
    session: AsyncSession,
    *,
    provider_id: int,
    model_name: str,
    display_name: str,
    model_type: str,
    context_window: int,
    is_default_chat: bool,
    is_default_emb: bool,
) -> bool:
    """Insert a single default model if missing; clear any prior default first.

    Returns True if a new row was created, False if it already existed.
    """
    existing = (
        await session.execute(
            select(ModelConfig).where(
                ModelConfig.provider_id == provider_id,
                ModelConfig.model_name == model_name,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        # Make sure the existing row carries the expected default flag, in
        # case it was seeded before the defaults contract was added.
        if is_default_chat and not existing.is_default_chat:
            await _clear_default_chat(session)
            existing.is_default_chat = True
        if is_default_emb and not existing.is_default_emb:
            await _clear_default_emb(session)
            existing.is_default_emb = True
        if existing.model_type != model_type:
            existing.model_type = model_type
        if existing.context_window != context_window:
            existing.context_window = context_window
        return False

    # Clear any existing default so the partial unique index stays consistent
    if is_default_chat:
        await _clear_default_chat(session)
    if is_default_emb:
        await _clear_default_emb(session)

    row = ModelConfig(
        provider_id=provider_id,
        model_name=model_name,
        display_name=display_name,
        model_type=model_type,
        context_window=context_window,
        enabled=True,
        is_default_chat=is_default_chat,
        is_default_emb=is_default_emb,
        extra_config={},
    )
    session.add(row)
    await session.flush()
    return True


async def _clear_default_chat(session: AsyncSession) -> None:
    rows = (
        await session.execute(
            select(ModelConfig).where(ModelConfig.is_default_chat.is_(True))
        )
    ).scalars().all()
    for r in rows:
        r.is_default_chat = False


async def _clear_default_emb(session: AsyncSession) -> None:
    rows = (
        await session.execute(
            select(ModelConfig).where(ModelConfig.is_default_emb.is_(True))
        )
    ).scalars().all()
    for r in rows:
        r.is_default_emb = False


if __name__ == "__main__":
    asyncio.run(main())