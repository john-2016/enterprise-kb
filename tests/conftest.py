"""Shared pytest fixtures for API integration tests.

Provides:
- session-scope DB initialization (init_db + create_all) so tables exist
  when the in-process app is hit via ASGITransport (which doesn't trigger
  the FastAPI lifespan handler).
- per-test AsyncClient wired to the in-process FastAPI app.
- admin / normal user fixtures + JWT tokens for them.
- Truncation of the 4 multi-model tables BEFORE each test so test data
  is fully isolated.

NOTE: Project-root ``conftest.py`` already adds ``/root/enterprise-kb`` to
``sys.path``, so ``import backend.*`` works here.
"""

from __future__ import annotations

import os
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

# Ensure encryption key is set before any crypto import happens.
os.environ.setdefault(
    "ENCRYPTION_KEY", "P1bpcHvVWWQ696WirBRFSyTPbdyeQGfv3-cNiM_-bEw"
)

from backend.config import settings  # noqa: E402
from backend.core.security import create_access_token, hash_password  # noqa: E402
from backend.database import Base, get_engine, init_db  # noqa: E402
from backend.main import app  # noqa: E402
from backend.models.user import User  # noqa: E402


_PHASE4_TABLES = (
    "ab_test_rules",
    "ab_test_metrics",
    "model_configs",
    "model_providers",
)


# ---------------------------------------------------------------------------
# Session-scope DB bootstrap (run in the same event loop as tests)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _bootstrap_database():
    """Initialize the async engine and create all tables once per session.

    ASGITransport does NOT trigger FastAPI's lifespan handler, so we must
    call ``init_db`` and ``create_all`` ourselves before the first request.
    """
    await init_db(settings.DATABASE_URL)
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


# ---------------------------------------------------------------------------
# Per-test cleanup
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(autouse=True)
async def _cleanup_phase4_tables():
    """Truncate the Phase 4 tables BEFORE each test for full isolation.

    Doing it before (not after) avoids fighting the test's open session
    over pool connections.
    """
    await _truncate_phase4()
    yield


async def _truncate_phase4() -> None:
    engine = get_engine()
    # Use a fresh connection so we don't collide with any per-test session.
    async with engine.connect() as conn:
        await conn.execute(
            text(
                "TRUNCATE TABLE "
                + ", ".join(_PHASE4_TABLES)
                + " RESTART IDENTITY CASCADE"
            )
        )
        await conn.commit()


# ---------------------------------------------------------------------------
# DB session / HTTP client
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """A real PG async session bound to the global engine."""
    engine = get_engine()
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as s:
        yield s


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    """An httpx AsyncClient wired to the in-process FastAPI app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ---------------------------------------------------------------------------
# Users + tokens
# ---------------------------------------------------------------------------


async def _get_or_create_user(
    db: AsyncSession, *, username: str, email: str, role: str
) -> User:
    result = await db.execute(select(User).where(User.username == username))
    u = result.scalar_one_or_none()
    if u is not None:
        return u
    u = User(
        username=username,
        email=email,
        hashed_password=hash_password("xxx"),
        role=role,
        is_active=True,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


@pytest_asyncio.fixture
async def admin_user(db_session: AsyncSession) -> User:
    return await _get_or_create_user(
        db_session,
        username="admin_test",
        email="admin@test.com",
        role="admin",
    )


@pytest_asyncio.fixture
async def admin_token(admin_user: User) -> str:
    return create_access_token(
        data={
            "sub": str(admin_user.id),
            "role": "admin",
            "username": admin_user.username,
        }
    )


@pytest_asyncio.fixture
async def normal_user(db_session: AsyncSession) -> User:
    return await _get_or_create_user(
        db_session,
        username="user_test",
        email="user@test.com",
        role="user",
    )


@pytest_asyncio.fixture
async def user_token(normal_user: User) -> str:
    return create_access_token(
        data={
            "sub": str(normal_user.id),
            "role": "user",
            "username": normal_user.username,
        }
    )


# ---------------------------------------------------------------------------
# Convenience: pre-built authorization header
# ---------------------------------------------------------------------------


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
