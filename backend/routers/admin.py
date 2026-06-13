"""
Admin router — user management, system statistics, and audit log viewer.

All routes require admin privileges and are prefixed with ``/api/v1/admin``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.deps import get_admin_user, get_db
from backend.routers.auth import RegisterRequest
from backend.services import auth_service

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class UserAdminResponse(BaseModel):
    id: int | str
    username: str
    email: str
    role: str
    is_active: bool
    created_at: datetime | str
    updated_at: datetime | str


class UserListResponse(BaseModel):
    items: list[UserAdminResponse]
    total: int
    skip: int
    limit: int


class UpdateRoleRequest(BaseModel):
    role: str = Field(..., pattern=r"^(admin|editor|viewer)$")


class SystemStats(BaseModel):
    user_count: int
    document_count: int
    chunk_count: int
    query_count: int


class AuditLogItem(BaseModel):
    id: int
    user_id: int
    username: str | None = None
    action: str
    target_type: str | None = None
    target_id: int | None = None
    details: str | None = None
    ip_address: str | None = None
    created_at: str


class AuditLogResponse(BaseModel):
    items: list[AuditLogItem]
    total: int
    skip: int
    limit: int


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/users",
    status_code=status.HTTP_201_CREATED,
    response_model=UserAdminResponse,
    summary="Create a new user (admin only)",
)
async def create_user(
    body: RegisterRequest,
    admin_user: dict[str, Any] = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Create a new user with the specified role (admin only)."""
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


@router.get(
    "/users",
    response_model=UserListResponse,
    summary="List all users",
)
async def list_all_users(
    skip: int = Query(0, ge=0, description="Records to skip"),
    limit: int = Query(100, ge=1, le=500, description="Max records to return"),
    admin_user: dict[str, Any] = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Return a paginated list of all registered users."""
    count_result = await db.execute(text("SELECT COUNT(*) FROM users"))
    total = count_result.scalar() or 0

    rows = await auth_service.list_users(db, skip=skip, limit=limit)

    return {
        "items": rows,
        "total": total,
        "skip": skip,
        "limit": limit,
    }


@router.put(
    "/users/{user_id}/role",
    response_model=UserAdminResponse,
    summary="Change a user's role",
)
async def change_user_role(
    user_id: int,
    body: UpdateRoleRequest,
    admin_user: dict[str, Any] = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        updated = await auth_service.update_user_role(
            db=db,
            user_id=int(user_id),
            new_role=body.role,
        )
    except auth_service.UserNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    return updated


@router.delete(
    "/users/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    summary="Delete a user (not yourself)",
)
async def delete_user(
    user_id: int,
    admin_user: dict[str, Any] = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    current_user_id = int(admin_user.get("sub"))
    if current_user_id == int(user_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete your own account. Use a different admin account.",
        )

    try:
        await auth_service.get_user_by_id(db, int(user_id))
    except auth_service.UserNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    await db.execute(
        text("DELETE FROM users WHERE id = :uid"),
        {"uid": int(user_id)},
    )
    await db.commit()


@router.get(
    "/stats",
    response_model=SystemStats,
    summary="System-wide statistics",
)
async def get_system_stats(
    admin_user: dict[str, Any] = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, int]:
    user_count = await db.execute(text("SELECT COUNT(*) FROM users"))
    doc_count = await db.execute(text("SELECT COUNT(*) FROM documents"))
    chunk_count = await db.execute(
        text("SELECT COALESCE(SUM(chunk_count), 0) FROM documents")
    )
    query_count = await db.execute(
        text("SELECT COUNT(*) FROM audit_logs WHERE action = 'query'")
    )

    return {
        "user_count": user_count.scalar() or 0,
        "document_count": doc_count.scalar() or 0,
        "chunk_count": chunk_count.scalar() or 0,
        "query_count": query_count.scalar() or 0,
    }


@router.get(
    "/audit-logs",
    response_model=AuditLogResponse,
    summary="Paginated audit log viewer",
)
async def get_audit_logs(
    skip: int = Query(0, ge=0, description="Records to skip"),
    limit: int = Query(50, ge=1, le=500, description="Max records to return"),
    action: str | None = Query(
        None, description="Filter by action type"
    ),
    admin_user: dict[str, Any] = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    params: dict[str, Any] = {"skip": skip, "limit": limit}

    if action:
        where_clause = "al.action = :action"
        params["action"] = action
    else:
        where_clause = "TRUE"

    count_result = await db.execute(
        text(f"SELECT COUNT(*) FROM audit_logs al WHERE {where_clause}"),
        params,
    )
    total = count_result.scalar() or 0

    result = await db.execute(
        text(f"""
        SELECT al.*, u.username
        FROM audit_logs al
        LEFT JOIN users u ON u.id = al.user_id
        WHERE {where_clause}
        ORDER BY al.created_at DESC
        OFFSET :skip LIMIT :limit
        """),
        params,
    )
    rows = result.mappings().all()

    items: list[dict[str, Any]] = []
    for row in rows:
        items.append(
            AuditLogItem(
                id=row["id"],
                user_id=row["user_id"],
                username=row.get("username"),
                action=row["action"],
                target_type=row.get("target_type"),
                target_id=row.get("target_id"),
                details=row.get("details"),
                ip_address=row.get("ip_address"),
                created_at=str(row.get("created_at", "")),
            )
        )

    return {
        "items": items,
        "total": total,
        "skip": skip,
        "limit": limit,
    }
