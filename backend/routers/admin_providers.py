"""Admin router — ModelProvider CRUD (Phase 4 Task 4.1).

Endpoints (all admin-only):
- GET    /api/v1/admin/providers         — list
- POST   /api/v1/admin/providers         — create (encrypts api_key)
- PATCH  /api/v1/admin/providers/{id}    — partial update
- DELETE /api/v1/admin/providers/{id}    — guarded delete

Security contract:
- The plaintext ``api_key`` is accepted on POST/PATCH and encrypted with
  Fernet before being persisted. Only ``key_last_4`` is ever returned in
  responses — the encrypted blob is never exposed.
- ``is_builtin=True`` rows are non-deletable.
- A provider referenced by any ``model_configs`` row is non-deletable
  (caller must reassign or delete the dependent models first).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.crypto import encrypt_key
from backend.core.deps import get_admin_user, get_db
from backend.models.model_config import ModelConfig
from backend.models.provider import ModelProvider

router = APIRouter(prefix="/api/v1/admin/providers", tags=["admin-providers"])


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class ProviderCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    display_name: str = Field(..., min_length=1, max_length=128)
    provider_type: str = Field(..., min_length=1, max_length=32)
    api_base_url: Optional[str] = Field(None, max_length=512)
    api_key: str = Field(..., min_length=1)
    extra_config: dict[str, Any] = Field(default_factory=dict)


class ProviderUpdate(BaseModel):
    display_name: Optional[str] = Field(None, min_length=1, max_length=128)
    api_base_url: Optional[str] = Field(None, max_length=512)
    api_key: Optional[str] = Field(None, min_length=1)
    extra_config: Optional[dict[str, Any]] = None
    enabled: Optional[bool] = None


class ProviderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    display_name: str
    provider_type: str
    api_base_url: Optional[str]
    key_last_4: str
    extra_config: dict[str, Any]
    enabled: bool
    is_builtin: bool
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_response(p: ModelProvider) -> ProviderResponse:
    """Build a ProviderResponse from an ORM row, deriving key_last_4
    by decrypting the stored blob. The encrypted blob itself is never
    placed on the response.
    """
    from backend.core.crypto import decrypt_key  # local import to avoid cycles

    try:
        plaintext = decrypt_key(p.api_key_enc)
        key_last_4 = plaintext[-4:] if len(plaintext) >= 4 else plaintext
    except Exception:  # pragma: no cover — DB-corruption path
        key_last_4 = "****"

    return ProviderResponse(
        id=p.id,
        name=p.name,
        display_name=p.display_name,
        provider_type=p.provider_type,
        api_base_url=p.api_base_url,
        key_last_4=key_last_4,
        extra_config=p.extra_config or {},
        enabled=p.enabled,
        is_builtin=p.is_builtin,
        created_at=p.created_at,
        updated_at=p.updated_at,
    )


async def _get_or_404(db: AsyncSession, provider_id: int) -> ModelProvider:
    p = await db.get(ModelProvider, provider_id)
    if p is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Provider {provider_id} not found",
        )
    return p


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=list[ProviderResponse],
    summary="List all model providers",
)
async def list_providers(
    admin_user: dict[str, Any] = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
) -> list[ProviderResponse]:
    rows = (
        await db.execute(select(ModelProvider).order_by(ModelProvider.id.asc()))
    ).scalars().all()
    return [_to_response(p) for p in rows]


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=ProviderResponse,
    summary="Create a new model provider",
)
async def create_provider(
    body: ProviderCreate,
    admin_user: dict[str, Any] = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
) -> ProviderResponse:
    # Duplicate-name guard
    existing = (
        await db.execute(select(ModelProvider).where(ModelProvider.name == body.name))
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Provider with name '{body.name}' already exists",
        )

    p = ModelProvider(
        name=body.name,
        display_name=body.display_name,
        provider_type=body.provider_type,
        api_base_url=body.api_base_url,
        api_key_enc=encrypt_key(body.api_key),
        extra_config=body.extra_config or {},
        enabled=True,
        is_builtin=False,
    )
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return _to_response(p)


@router.patch(
    "/{provider_id}",
    response_model=ProviderResponse,
    summary="Partially update a provider",
)
async def update_provider(
    provider_id: int,
    body: ProviderUpdate,
    admin_user: dict[str, Any] = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
) -> ProviderResponse:
    p = await _get_or_404(db, provider_id)

    if body.display_name is not None:
        p.display_name = body.display_name
    if body.api_base_url is not None:
        p.api_base_url = body.api_base_url
    if body.api_key is not None:
        p.api_key_enc = encrypt_key(body.api_key)
    if body.extra_config is not None:
        p.extra_config = body.extra_config
    if body.enabled is not None:
        p.enabled = body.enabled

    await db.commit()
    await db.refresh(p)
    return _to_response(p)


@router.delete(
    "/{provider_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    summary="Delete a provider (guarded)",
)
async def delete_provider(
    provider_id: int,
    admin_user: dict[str, Any] = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    p = await _get_or_404(db, provider_id)

    if p.is_builtin:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Built-in providers cannot be deleted",
        )

    # FK reference guard — check model_configs directly
    ref_count = (
        await db.execute(
            select(func.count())
            .select_from(ModelConfig)
            .where(ModelConfig.provider_id == provider_id)
        )
    ).scalar_one()
    if ref_count and ref_count > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Provider {provider_id} is referenced by "
                f"{ref_count} model_config(s); delete or reassign those first"
            ),
        )

    await db.delete(p)
    await db.commit()
