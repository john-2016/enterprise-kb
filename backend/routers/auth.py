"""
Authentication router — register, login, profile, and password change.

All routes are prefixed with ``/api/v1/auth``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.deps import get_current_user, get_db
from backend.core.security import create_access_token, hash_password
from backend.services import auth_service

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=2, max_length=150)
    email: EmailStr
    password: str = Field(..., min_length=6, max_length=255)
    role: str = Field(default="editor", pattern=r"^(admin|editor|viewer)$")


class LoginRequest(BaseModel):
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(..., min_length=6, max_length=255)


class UserResponse(BaseModel):
    id: str
    username: str
    email: str
    role: str
    is_active: bool
    created_at: str
    updated_at: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    response_model=UserResponse,
    summary="Register a new user",
)
async def register(
    body: RegisterRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Create a new user account with the given credentials and role."""
    try:
        user = await auth_service.register_user(
            db=db,
            username=body.username,
            email=body.email,
            password=body.password,
            role=body.role,
        )
    except auth_service.AuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    return user


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Authenticate and receive a JWT",
)
async def login(
    body: LoginRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Authenticate with username/password and return a JWT access token."""
    try:
        user = await auth_service.authenticate_user(
            db=db,
            username=body.username,
            password=body.password,
        )
    except auth_service.AuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    token = create_access_token(
        data={
            "sub": str(user["id"]),
            "username": user["username"],
            "email": user["email"],
            "role": user["role"],
        }
    )

    return {
        "access_token": token,
        "token_type": "bearer",
        "user": user,
    }


@router.get(
    "/me",
    response_model=UserResponse,
    summary="Get current authenticated user's profile",
)
async def get_me(
    current_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Return the profile of the currently authenticated user."""
    user_id = current_user.get("sub")
    try:
        user = await auth_service.get_user_by_id(db, user_id)
    except auth_service.UserNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    return user


@router.put(
    "/me/password",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Change current user's password",
)
async def change_password(
    body: ChangePasswordRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Update the authenticated user's password.

    Requires the current password for verification.
    """
    user_id = current_user.get("sub")

    # Fetch the user with the hashed password
    result = await db.execute(
        "SELECT * FROM users WHERE id = :uid",
        {"uid": user_id},
    )
    user_row = result.mappings().first()
    if user_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Verify current password using the service's internal verifier
    from backend.core.security import verify_password

    if not verify_password(body.current_password, user_row["hashed_password"]):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        )

    # Update the password
    new_hashed = hash_password(body.new_password)
    await db.execute(
        "UPDATE users SET hashed_password = :hp, updated_at = :now WHERE id = :uid",
        {
            "hp": new_hashed,
            "now": "datetime('now')",
            "uid": user_id,
        },
    )
    await db.commit()
