"""Authentication and user management service.

Uses passlib with bcrypt for password hashing. All database operations
are async-compatible (accept an async db session).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from passlib.context import CryptContext
from sqlalchemy import text

# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------

_pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")


def _hash_password(password: str) -> str:
    return _pwd_ctx.hash(password)


def _verify_password(plain: str, hashed: str) -> bool:
    return _pwd_ctx.verify(plain, hashed)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class AuthError(Exception):
    """Raised on authentication failures."""


class UserNotFoundError(Exception):
    """Raised when a user is not found."""


# ---------------------------------------------------------------------------
# User helpers (dict-based stub — replace with real ORM models in production)
# ---------------------------------------------------------------------------

def _make_user(
    username: str,
    email: str,
    password: str,
    role: str = "editor",
) -> dict:
    return {
        "username": username,
        "email": email,
        "hashed_password": _hash_password(password),
        "role": role,
        "is_active": True,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def register_user(
    db,
    username: str,
    email: str,
    password: str,
    role: str = "editor",
) -> dict:
    """Register a new user.

    Parameters
    ----------
    db : Any
        Async database session (SQLAlchemy async or similar).
    username, email, password : str
        User credentials.
    role : str
        One of ``"admin"``, ``"editor"`` (default), ``"viewer"``.

    Returns
    -------
    dict
        The newly created user record (password hash excluded).

    Raises
    ------
    AuthError
        If the username or email already exists.
    """
    # Simulate a uniqueness check
    existing = await db.execute(
        text("SELECT id FROM users WHERE username = :u OR email = :e"),
        {"u": username, "e": email},
    )
    if existing is not None and existing.scalar_one_or_none() is not None:
        raise AuthError("Username or email already registered")

    user = _make_user(username, email, password, role)

    result = await db.execute(
        text("INSERT INTO users (username, email, hashed_password, role, "
             "is_active, created_at, updated_at) "
             "VALUES (:username, :email, :hashed_password, :role, "
             ":is_active, :created_at, :updated_at) RETURNING *"),
        user,
    )
    await db.commit()

    # Fetch the auto-generated id from RETURNING
    row = result.mappings().first()
    if row:
        user["id"] = row["id"]

    # Return record without the password hash
    return {k: v for k, v in user.items() if k != "hashed_password"}


async def authenticate_user(
    db,
    username: str,
    password: str,
) -> dict:
    """Authenticate a user by username and password.

    Returns
    -------
    dict
        User record (password hash excluded).

    Raises
    ------
    AuthError
        If credentials are invalid or the user is inactive.
    """
    result = await db.execute(
        text("SELECT * FROM users WHERE username = :u"),
        {"u": username},
    )
    user = result.mappings().first()
    if user is None:
        raise AuthError("Invalid username or password")

    if not _verify_password(password, user["hashed_password"]):
        raise AuthError("Invalid username or password")

    if not user.get("is_active", True):
        raise AuthError("Account is deactivated")

    return {k: v for k, v in user.items() if k != "hashed_password"}


async def get_user_by_id(
    db,
    user_id: int,
) -> dict:
    """Retrieve a user by their primary key.

    Raises
    ------
    UserNotFoundError
    """
    result = await db.execute(
        text("SELECT * FROM users WHERE id = :uid"),
        {"uid": int(user_id)},
    )
    user = result.mappings().first()
    if user is None:
        raise UserNotFoundError(f"User {user_id} not found")
    return {k: v for k, v in user.items() if k != "hashed_password"}


async def list_users(
    db,
    skip: int = 0,
    limit: int = 100,
) -> list[dict]:
    """List users with pagination.

    Returns
    -------
    list[dict]
        User records (password hashes excluded).
    """
    result = await db.execute(
        text("SELECT * FROM users ORDER BY created_at DESC "
             "OFFSET :skip LIMIT :limit"),
        {"skip": skip, "limit": limit},
    )
    rows = result.mappings().all()
    return [{k: v for k, v in row.items() if k != "hashed_password"} for row in rows]


async def update_user_role(
    db,
    user_id: int,
    new_role: str,
) -> dict:
    """Update a user's role.

    Parameters
    ----------
    new_role : str
        One of ``"admin"``, ``"editor"``, ``"viewer"``.

    Raises
    ------
    UserNotFoundError
    """
    valid_roles = {"admin", "editor", "viewer"}
    if new_role not in valid_roles:
        raise ValueError(f"Invalid role '{new_role}'. Must be one of {valid_roles}")

    result = await db.execute(
        text("UPDATE users SET role = :role, updated_at = :now "
             "WHERE id = :uid RETURNING *"),
        {"role": new_role, "now": datetime.now(timezone.utc), "uid": int(user_id)},
    )
    updated = result.mappings().first()
    if updated is None:
        raise UserNotFoundError(f"User {user_id} not found")

    await db.commit()
    return {k: v for k, v in updated.items() if k != "hashed_password"}
