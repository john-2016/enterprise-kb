"""
FastAPI dependency injection helpers.
"""

from __future__ import annotations

from typing import AsyncGenerator

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.security import decode_access_token
from backend import database

# Bearer-token scheme used by the OpenAPI docs UI
bearer_scheme = HTTPBearer(auto_error=False)


# ---------------------------------------------------------------------------
# Database session
# ---------------------------------------------------------------------------

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields an async database session.

    The session is committed on success or rolled back on exception.
    """
    async with database.AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# ---------------------------------------------------------------------------
# Current user (JWT-protected)
# ---------------------------------------------------------------------------

async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Return the current user payload decoded from the Bearer JWT,
    并把 role 字段**重新从数据库**取（不信任 token 里的 role）。

    Raises ``401 UNAUTHORIZED`` if the token is missing or invalid.
    Raises ``401`` if the user no longer exists in the DB.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_access_token(credentials.credentials)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 把 role 重新从 DB 查（防止提权 / 角色降级后旧 token 仍生效）
    sub_raw = payload.get("sub")
    try:
        user_id = int(sub_raw)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token subject",
        )

    row = await db.execute(
        text("SELECT id, username, email, role, is_active FROM users WHERE id = :uid"),
        {"uid": user_id},
    )
    user = row.mappings().first()
    if user is None or not user["is_active"]:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
        )

    # 合并：token 的 sub/exp + DB 查到的 role
    return {
        "sub": str(user["id"]),
        "username": user["username"],
        "email": user["email"],
        "role": user["role"],
    }


# ---------------------------------------------------------------------------
# Admin user guard
# ---------------------------------------------------------------------------

async def get_admin_user(
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Ensure the authenticated user has the ``admin`` role."""
    role = (current_user.get("role") or "").lower()
    if role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required",
        )
    return current_user
