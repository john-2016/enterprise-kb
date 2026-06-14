"""Admin router — A/B Test Rule CRUD (Phase 4 Task 4.3).

Endpoints (all admin-only):
- GET    /api/v1/admin/ab-rules
- POST   /api/v1/admin/ab-rules
- PATCH  /api/v1/admin/ab-rules/{id}
- DELETE /api/v1/admin/ab-rules/{id}

策略 / 目标白名单：
- strategy ∈ {"user_hash_mod", "random_weight"}
- target   ∈ {"chat", "embedding"}

POST 时校验 config：
- user_hash_mod: 形如 ``{"mod": N, "mapping": {"0": "m1", ..., "N-1": "mN"}}``，
  mapping 的所有 model_name 必须在 ``model_configs`` 中存在且 enabled=True。
- random_weight: 形如 ``{"weights": {"m1": 0.7, ...}}``，weights 之和应 ≈ 1.0
  （容差 0.01）。

设计要点：
- 校验失败统一返回 400 + 详细 detail，便于前端直接展示。
- DELETE / PATCH 不存在 id → 404。
- 加密/解密不在此 router（rule 不持有密钥，只引用 model_name）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

_PYDANTIC_CONFIG = ConfigDict(protected_namespaces=())
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.deps import get_admin_user, get_db
from backend.models.ab_test import ABTestRule
from backend.models.model_config import ModelConfig

router = APIRouter(prefix="/api/v1/admin/ab-rules", tags=["admin-ab-rules"])

# 业务枚举白名单
_ALLOWED_STRATEGIES = frozenset({"user_hash_mod", "random_weight"})
_ALLOWED_TARGETS = frozenset({"chat", "embedding"})
_WEIGHT_SUM_TOLERANCE = 0.01


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class ABRuleCreate(BaseModel):
    model_config = _PYDANTIC_CONFIG
    """创建 A/B 规则请求体。"""

    name: str = Field(..., min_length=1, max_length=128)
    enabled: bool = True
    strategy: str
    target: str
    config: dict[str, Any]
    description: Optional[str] = None


class ABRuleUpdate(BaseModel):
    model_config = _PYDANTIC_CONFIG
    """部分更新 A/B 规则 — 所有字段可选。"""

    name: Optional[str] = Field(None, min_length=1, max_length=128)
    enabled: Optional[bool] = None
    strategy: Optional[str] = None
    target: Optional[str] = None
    config: Optional[dict[str, Any]] = None
    description: Optional[str] = None


class ABRuleResponse(BaseModel):
    """A/B 规则响应。"""

    model_config = ConfigDict(from_attributes=True, protected_namespaces=())

    id: int
    name: str
    enabled: bool
    strategy: str
    target: str
    config: dict[str, Any]
    description: Optional[str]
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# 校验 helpers
# ---------------------------------------------------------------------------


async def _validate_strategy_target(
    strategy: str, target: str
) -> None:
    """strategy / target 白名单校验，失败抛 400。"""
    if strategy not in _ALLOWED_STRATEGIES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"strategy must be one of {sorted(_ALLOWED_STRATEGIES)}, "
                f"got {strategy!r}"
            ),
        )
    if target not in _ALLOWED_TARGETS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"target must be one of {sorted(_ALLOWED_TARGETS)}, "
                f"got {target!r}"
            ),
        )


async def _validate_config(
    db: AsyncSession, *, strategy: str, config: dict[str, Any]
) -> None:
    """按 strategy 校验 config 的内部结构 + 跨表一致性。"""
    if strategy == "user_hash_mod":
        await _validate_user_hash_mod(db, config)
    elif strategy == "random_weight":
        _validate_random_weight(config)
    # 其余 strategy 已被 _validate_strategy_target 拦截


async def _validate_user_hash_mod(
    db: AsyncSession, config: dict[str, Any]
) -> None:
    """user_hash_mod config 必须有 ``mod`` (int>0) 和 ``mapping`` (dict)。

    mapping 的所有 value 必须在 ``model_configs`` 表存在且 enabled=True。
    """
    if not isinstance(config, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="config must be a dict for user_hash_mod",
        )

    mod = config.get("mod")
    if not isinstance(mod, int) or mod <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="user_hash_mod config.mod must be a positive int",
        )

    mapping = config.get("mapping")
    if not isinstance(mapping, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="user_hash_mod config.mapping must be a dict",
        )

    # 检查所有 mapping key 是字符串数字 0..mod-1
    expected_keys = {str(i) for i in range(mod)}
    actual_keys = set(mapping.keys())
    if actual_keys != expected_keys:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"user_hash_mod mapping keys must be exactly "
                f"{sorted(expected_keys)} (mod={mod}), "
                f"got {sorted(actual_keys)}"
            ),
        )

    # 检查 mapping 的所有 value 是非空字符串
    model_names: list[str] = []
    for k, v in mapping.items():
        if not isinstance(v, str) or not v:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"user_hash_mod mapping[{k!r}] must be a non-empty "
                    "model_name string"
                ),
            )
        model_names.append(v)

    # 跨表一致性：model_name 必须在 model_configs 存在且 enabled=True
    rows = (
        await db.execute(
            select(ModelConfig.model_name, ModelConfig.enabled).where(
                ModelConfig.model_name.in_(model_names)
            )
        )
    ).all()
    found = {row.model_name: row.enabled for row in rows}

    missing = [n for n in model_names if n not in found]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"user_hash_mod mapping references unknown model_name(s): "
                f"{sorted(set(missing))}"
            ),
        )
    disabled = [n for n in model_names if found.get(n) is False]
    if disabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"user_hash_mod mapping references disabled model_name(s): "
                f"{sorted(set(disabled))}"
            ),
        )


def _validate_random_weight(config: dict[str, Any]) -> None:
    """random_weight config 必须有 ``weights`` (dict)，各 value 之和 ≈ 1.0。"""
    if not isinstance(config, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="config must be a dict for random_weight",
        )

    weights = config.get("weights")
    if not isinstance(weights, dict) or not weights:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "random_weight config.weights must be a non-empty dict of "
                "{model_name: float}"
            ),
        )

    total = 0.0
    for k, v in weights.items():
        if not isinstance(v, (int, float)) or v < 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"random_weight weights[{k!r}] must be a non-negative "
                    "number"
                ),
            )
        total += float(v)

    if abs(total - 1.0) > _WEIGHT_SUM_TOLERANCE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"random_weight weights sum must be 1.0 (±{_WEIGHT_SUM_TOLERANCE}), "
                f"got {total}"
            ),
        )


# ---------------------------------------------------------------------------
# 通用 helpers
# ---------------------------------------------------------------------------


async def _get_or_404(db: AsyncSession, rule_id: int) -> ABTestRule:
    rule = await db.get(ABTestRule, rule_id)
    if rule is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"AB rule {rule_id} not found",
        )
    return rule


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=list[ABRuleResponse],
    summary="列出所有 A/B 测试规则",
)
async def list_ab_rules(
    admin_user: dict[str, Any] = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
) -> list[ABRuleResponse]:
    """列出所有 A/B 规则（按 id 升序）。"""
    rows = (
        await db.execute(select(ABTestRule).order_by(ABTestRule.id.asc()))
    ).scalars().all()
    return [
        ABRuleResponse.model_validate(r) for r in rows
    ]


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=ABRuleResponse,
    summary="创建一条 A/B 测试规则",
)
async def create_ab_rule(
    body: ABRuleCreate,
    admin_user: dict[str, Any] = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
) -> ABRuleResponse:
    """创建 A/B 规则；strategy/target/config 校验失败 → 400。"""
    await _validate_strategy_target(body.strategy, body.target)
    await _validate_config(db, strategy=body.strategy, config=body.config)

    rule = ABTestRule(
        name=body.name,
        enabled=body.enabled,
        strategy=body.strategy,
        target=body.target,
        config=body.config,
        description=body.description,
    )
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    return ABRuleResponse.model_validate(rule)


@router.patch(
    "/{rule_id}",
    response_model=ABRuleResponse,
    summary="部分更新一条 A/B 规则",
)
async def update_ab_rule(
    rule_id: int,
    body: ABRuleUpdate,
    admin_user: dict[str, Any] = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
) -> ABRuleResponse:
    """部分更新 A/B 规则；如果同时改了 strategy/target/config，会重新校验。"""
    rule = await _get_or_404(db, rule_id)

    # 计算"生效后的" strategy/target/config 用于联合校验
    new_strategy = body.strategy if body.strategy is not None else rule.strategy
    new_target = body.target if body.target is not None else rule.target
    new_config = body.config if body.config is not None else rule.config

    await _validate_strategy_target(new_strategy, new_target)
    await _validate_config(db, strategy=new_strategy, config=new_config)

    if body.name is not None:
        rule.name = body.name
    if body.enabled is not None:
        rule.enabled = body.enabled
    if body.strategy is not None:
        rule.strategy = body.strategy
    if body.target is not None:
        rule.target = body.target
    if body.config is not None:
        rule.config = body.config
    if body.description is not None:
        rule.description = body.description

    await db.commit()
    await db.refresh(rule)
    return ABRuleResponse.model_validate(rule)


@router.delete(
    "/{rule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    summary="删除一条 A/B 规则",
)
async def delete_ab_rule(
    rule_id: int,
    admin_user: dict[str, Any] = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """删除 A/B 规则（不存在 → 404）。注意：历史 metrics 行 ab_rule_id 会被 SET NULL。"""
    rule = await _get_or_404(db, rule_id)
    await db.delete(rule)
    await db.commit()
