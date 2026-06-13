"""
Security utilities: JWT token creation / verification and password hashing.

直接使用 bcrypt 库（不经过 passlib），避免 passlib 1.7.4 + bcrypt 5.x 兼容性告警。
"""

from __future__ import annotations

import bcrypt
from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt

from backend.config import settings

# ---------------------------------------------------------------------------
# Password hashing — 直接调用 bcrypt（避免 passlib 的 __about__ 探测）
# ---------------------------------------------------------------------------

def hash_password(password: str) -> str:
    """Return the bcrypt hash of *password* (UTF-8, 截断 72 字节)。"""
    pw = password.encode("utf-8")[:72]
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(pw, salt).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Check *plain_password* against its stored bcrypt hash。"""
    if not hashed_password:
        return False
    try:
        pw = plain_password.encode("utf-8")[:72]
        return bcrypt.checkpw(pw, hashed_password.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------

def create_access_token(
    data: dict[str, Any],
    expires_delta: timedelta | None = None,
) -> str:
    """Encode *data* into a signed JWT access token.

    强制写入 iss / aud claim，用于后续解码校验。
    强制把 sub 转为 str（JWT 标准要求）。
    """
    to_encode = data.copy()
    if "sub" in to_encode and not isinstance(to_encode["sub"], str):
        to_encode["sub"] = str(to_encode["sub"])
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.JWT_EXPIRATION_MINUTES)
    )
    to_encode.update({
        "exp": expire,
        "iss": settings.JWT_ISSUER,
        "aud": settings.JWT_AUDIENCE,
    })
    return jwt.encode(
        to_encode,
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )


def decode_access_token(token: str) -> dict[str, Any] | None:
    """Decode and verify *token*.  Return the payload dict, or ``None`` on
    any validation failure (expired, bad signature, malformed, missing
    required claims, wrong iss/aud, …)."""
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            issuer=settings.JWT_ISSUER,
            audience=settings.JWT_AUDIENCE,
            options={"require": ["exp", "iss", "aud", "sub"]},
        )
        return payload  # type: ignore[no-any-return]
    except JWTError:
        return None
