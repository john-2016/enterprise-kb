"""Admin router — ModelConfig CRUD (Phase 4 Task 4.2).

Endpoints (all admin-only):
- GET    /api/v1/admin/models
- POST   /api/v1/admin/models
- PATCH  /api/v1/admin/models/{id}
- DELETE /api/v1/admin/models/{id}

Invariant:
- Only one row across the table may carry ``is_default_chat = True``,
  and only one may carry ``is_default_emb = True``. PATCHing either
  flag atomically clears the previous holder.

Embedding default swap returns a ``warning`` field — switching the
embedding model requires a vector-store rebuild.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

_PYDANTIC_CONFIG = ConfigDict(protected_namespaces=())
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.deps import get_admin_user, get_db
from backend.models.model_config import ModelConfig
from backend.models.provider import ModelProvider

router = APIRouter(prefix="/api/v1/admin/models", tags=["admin-models"])

_ALLOWED_MODEL_TYPES = {"chat", "embedding"}


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class ModelCreate(BaseModel):
    model_config = _PYDANTIC_CONFIG
    provider_id: int
    model_name: str = Field(..., min_length=1, max_length=128)
    display_name: str = Field(..., min_length=1, max_length=128)
    model_type: str
    context_window: int = Field(128000, ge=1)
    is_default_chat: bool = False
    is_default_emb: bool = False
    extra_config: dict[str, Any] = Field(default_factory=dict)

    @field_validator("model_type")
    @classmethod
    def _v_type(cls, v: str) -> str:
        if v not in _ALLOWED_MODEL_TYPES:
            raise ValueError(
                f"model_type must be one of {sorted(_ALLOWED_MODEL_TYPES)}"
            )
        return v


class ModelUpdate(BaseModel):
    model_config = _PYDANTIC_CONFIG
    display_name: Optional[str] = Field(None, min_length=1, max_length=128)
    model_type: Optional[str] = None
    context_window: Optional[int] = Field(None, ge=1)
    enabled: Optional[bool] = None
    is_default_chat: Optional[bool] = None
    is_default_emb: Optional[bool] = None
    extra_config: Optional[dict[str, Any]] = None

    @field_validator("model_type")
    @classmethod
    def _v_type(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if v not in _ALLOWED_MODEL_TYPES:
            raise ValueError(
                f"model_type must be one of {sorted(_ALLOWED_MODEL_TYPES)}"
            )
        return v


class ModelResponse(BaseModel):
    # Phase 7 fix: 合并 from_attributes + protected_namespaces=()
    model_config = ConfigDict(from_attributes=True, protected_namespaces=())

    id: int
    provider_id: int
    model_name: str
    display_name: str
    model_type: str
    context_window: int
    enabled: bool
    is_default_chat: bool
    is_default_emb: bool
    extra_config: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class ModelPatchResponse(ModelResponse):
    """Same as ModelResponse, with an optional warning that surfaces
    when an embedding default swap requires a vector-store rebuild."""
    warning: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_or_404(db: AsyncSession, model_id: int) -> ModelConfig:
    m = await db.get(ModelConfig, model_id)
    if m is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Model {model_id} not found",
        )
    return m


def _to_response(m: ModelConfig, **extra) -> dict[str, Any]:
    base = {
        "id": m.id,
        "provider_id": m.provider_id,
        "model_name": m.model_name,
        "display_name": m.display_name,
        "model_type": m.model_type,
        "context_window": m.context_window,
        "enabled": m.enabled,
        "is_default_chat": m.is_default_chat,
        "is_default_emb": m.is_default_emb,
        "extra_config": m.extra_config or {},
        "created_at": m.created_at,
        "updated_at": m.updated_at,
    }
    base.update(extra)
    return base


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=list[ModelResponse],
    summary="List all model configs",
)
async def list_models(
    admin_user: dict[str, Any] = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
) -> list[ModelConfig]:
    rows = (
        await db.execute(select(ModelConfig).order_by(ModelConfig.id.asc()))
    ).scalars().all()
    return rows  # FastAPI will serialize via ModelResponse


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=ModelResponse,
    summary="Create a new model config",
)
async def create_model(
    body: ModelCreate,
    admin_user: dict[str, Any] = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
) -> Any:
    prov = await db.get(ModelProvider, body.provider_id)
    if prov is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Provider {body.provider_id} does not exist",
        )

    # If this row is marked as default for its type, clear any existing
    # default in the same type so the partial-unique index holds.
    if body.is_default_chat:
        await _clear_default_chat(db, except_id=None)
    if body.is_default_emb:
        await _clear_default_emb(db, except_id=None)

    m = ModelConfig(
        provider_id=body.provider_id,
        model_name=body.model_name,
        display_name=body.display_name,
        model_type=body.model_type,
        context_window=body.context_window,
        enabled=True,
        is_default_chat=body.is_default_chat,
        is_default_emb=body.is_default_emb,
        extra_config=body.extra_config or {},
    )
    db.add(m)
    try:
        await db.commit()
    except Exception as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Could not create model: {exc}",
        ) from exc
    await db.refresh(m)
    return m


@router.patch(
    "/{model_id}",
    response_model=ModelPatchResponse,
    summary="Partially update a model config",
)
async def update_model(
    model_id: int,
    body: ModelUpdate,
    admin_user: dict[str, Any] = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
) -> Any:
    m = await _get_or_404(db, model_id)

    warning: Optional[str] = None

    if body.display_name is not None:
        m.display_name = body.display_name
    if body.model_type is not None:
        m.model_type = body.model_type
    if body.context_window is not None:
        m.context_window = body.context_window
    if body.enabled is not None:
        m.enabled = body.enabled
    if body.extra_config is not None:
        m.extra_config = body.extra_config

    # Default-flag handling — atomically swap the holder.
    if body.is_default_chat is True and not m.is_default_chat:
        await _clear_default_chat(db, except_id=m.id)
        m.is_default_chat = True
    elif body.is_default_chat is False:
        m.is_default_chat = False

    if body.is_default_emb is True and not m.is_default_emb:
        existing_default_id = await _current_default_emb_id(db, except_id=m.id)
        if existing_default_id is not None:
            warning = (
                "切换默认 embedding 模型需要重建向量库；"
                "现有向量索引继续基于旧模型工作直到重建完成"
            )
        await _clear_default_emb(db, except_id=m.id)
        m.is_default_emb = True
    elif body.is_default_emb is False:
        m.is_default_emb = False

    await db.commit()
    await db.refresh(m)

    if warning is not None:
        return ModelPatchResponse(
            id=m.id,
            provider_id=m.provider_id,
            model_name=m.model_name,
            display_name=m.display_name,
            model_type=m.model_type,
            context_window=m.context_window,
            enabled=m.enabled,
            is_default_chat=m.is_default_chat,
            is_default_emb=m.is_default_emb,
            extra_config=m.extra_config or {},
            created_at=m.created_at,
            updated_at=m.updated_at,
            warning=warning,
        )
    return ModelPatchResponse(
        id=m.id,
        provider_id=m.provider_id,
        model_name=m.model_name,
        display_name=m.display_name,
        model_type=m.model_type,
        context_window=m.context_window,
        enabled=m.enabled,
        is_default_chat=m.is_default_chat,
        is_default_emb=m.is_default_emb,
        extra_config=m.extra_config or {},
        created_at=m.created_at,
        updated_at=m.updated_at,
    )


@router.delete(
    "/{model_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    summary="Delete a model config",
)
async def delete_model(
    model_id: int,
    admin_user: dict[str, Any] = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    m = await _get_or_404(db, model_id)
    await db.delete(m)
    await db.commit()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _clear_default_chat(db: AsyncSession, *, except_id: Optional[int]) -> None:
    stmt = select(ModelConfig).where(ModelConfig.is_default_chat.is_(True))
    if except_id is not None:
        stmt = stmt.where(ModelConfig.id != except_id)
    rows = (await db.execute(stmt)).scalars().all()
    for r in rows:
        r.is_default_chat = False
    await db.flush()


async def _clear_default_emb(db: AsyncSession, *, except_id: Optional[int]) -> None:
    stmt = select(ModelConfig).where(ModelConfig.is_default_emb.is_(True))
    if except_id is not None:
        stmt = stmt.where(ModelConfig.id != except_id)
    rows = (await db.execute(stmt)).scalars().all()
    for r in rows:
        r.is_default_emb = False
    await db.flush()


async def _current_default_emb_id(
    db: AsyncSession, *, except_id: Optional[int]
) -> Optional[int]:
    stmt = select(ModelConfig.id).where(ModelConfig.is_default_emb.is_(True))
    if except_id is not None:
        stmt = stmt.where(ModelConfig.id != except_id)
    return (await db.execute(stmt)).scalar_one_or_none()
