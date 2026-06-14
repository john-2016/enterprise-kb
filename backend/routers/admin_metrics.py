"""Admin router — Metrics 聚合 + Connectivity Test (Phase 4 Task 4.4)。

Endpoints (all admin-only):
- GET  /api/v1/admin/metrics/summary?days=N
    按 ``model_id`` 聚合最近 N 天的 ``ab_test_metrics``，返回每个模型的
    调用数 / 平均延迟 / token / 用户反馈 / 满意度，并选出满意度最高的
    model_name 作为 winner。
- POST /api/v1/admin/models/test
    Body: ``{provider_id, model_name, test_message?}``
    解密 provider key → 构造 UnifiedModelClient → 发一条 chat；任何异常
    都包成 ``{success: False, error: ...}`` 返回，**绝不抛 500**。

设计要点：
- SQL 聚合做 counts / sums / avg，再 Python 计算 satisfaction_rate（避免在
  PG 里直接做条件 sum 时的可读性问题，并兼容分母=0 的边界）。
- winner：满意度最高的模型名；并列时取最早 id（SQL 已经按 model_id 升序聚合）。
- Connectivity test 是 admin 调试入口，但必须容错：第三方 provider 抛任何
  exception（401、timeout、网络）都要转成 success=False 的结构化响应。
"""

from __future__ import annotations

import time
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

_PYDANTIC_CONFIG = ConfigDict(protected_namespaces=())
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.crypto import decrypt_key
from backend.core.deps import get_admin_user, get_db
from backend.models.ab_test import ABTestMetric
from backend.models.model_config import ModelConfig
from backend.models.provider import ModelProvider
from backend.services.model_clients.base import ChatMessage
from backend.services.model_clients.factory import get_client

router = APIRouter(
    prefix="/api/v1/admin",
    tags=["admin-metrics", "admin-models-test"],
)


class _ProviderProxy:
    """把 SQLAlchemy ORM ``ModelProvider`` 的 ``Mapped[str]`` 字段解包成
    plain ``str``，以满足 ``_ProviderLike`` Protocol 的类型签名。
    """

    def __init__(self, p: ModelProvider) -> None:
        self.provider_type = str(p.provider_type)
        self.api_base_url = p.api_base_url  # may be None


def _provider_proxy(p: ModelProvider) -> _ProviderProxy:
    return _ProviderProxy(p)


# ---------------------------------------------------------------------------
# Pydantic schemas — Summary
# ---------------------------------------------------------------------------


class ModelMetricRow(BaseModel):
    model_config = _PYDANTIC_CONFIG
    """单个模型在指定时间窗口内的聚合指标。"""

    model_id: int
    model_name: str
    total_calls: int
    avg_latency_ms: int
    total_input_tokens: int
    total_output_tokens: int
    positive_feedback: int
    negative_feedback: int
    satisfaction_rate: float


class MetricsSummaryResponse(BaseModel):
    model_config = _PYDANTIC_CONFIG
    """Metrics summary 响应。"""

    period_days: int
    models: list[ModelMetricRow]
    winner: Optional[str] = None


# ---------------------------------------------------------------------------
# Pydantic schemas — Connectivity test
# ---------------------------------------------------------------------------


class ConnectivityTestRequest(BaseModel):
    model_config = _PYDANTIC_CONFIG
    """Connectivity 测试请求体。"""

    provider_id: int
    model_name: str = Field(..., min_length=1)
    test_message: str = "hi"


class ConnectivityTestResponse(BaseModel):
    model_config = _PYDANTIC_CONFIG
    """Connectivity 测试响应（统一结构，失败时仍返回 200 + success=False）。"""

    success: bool
    latency_ms: Optional[int] = None
    model: Optional[str] = None
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Summary endpoint
# ---------------------------------------------------------------------------


@router.get(
    "/metrics/summary",
    response_model=MetricsSummaryResponse,
    summary="聚合 A/B 测试指标（按 model_id 分组）",
)
async def get_metrics_summary(
    days: int = 7,
    admin_user: dict[str, Any] = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
) -> MetricsSummaryResponse:
    """聚合最近 ``days`` 天的 ``ab_test_metrics``。

    SQL 一次性算好 counts / sums / avg_latency，再在 Python 里算
    satisfaction_rate（处理分母=0）。
    """
    if days <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="days must be a positive integer",
        )

    # 1) 聚合：按 model_id 算 total_calls / avg_latency / tokens / 反馈计数
    #    注意 feedback=1 为正，=-1 为负，0/None 忽略
    pos = func.sum(
        case((ABTestMetric.feedback == 1, 1), else_=0)
    ).label("positive_feedback")
    neg = func.sum(
        case((ABTestMetric.feedback == -1, 1), else_=0)
    ).label("negative_feedback")

    cutoff_sql = func.now() - func.make_interval(0, 0, 0, days, 0, 0, 0)

    agg_rows = (
        await db.execute(
            select(
                ABTestMetric.model_id.label("model_id"),
                func.count().label("total_calls"),
                func.avg(ABTestMetric.latency_ms).label("avg_latency_ms"),
                func.coalesce(func.sum(ABTestMetric.input_tokens), 0).label(
                    "total_input_tokens"
                ),
                func.coalesce(func.sum(ABTestMetric.output_tokens), 0).label(
                    "total_output_tokens"
                ),
                pos,
                neg,
            )
            .where(ABTestMetric.created_at >= cutoff_sql)
            .group_by(ABTestMetric.model_id)
            .order_by(ABTestMetric.model_id.asc())
        )
    ).all()

    if not agg_rows:
        return MetricsSummaryResponse(period_days=days, models=[], winner=None)

    # 2) 取这些 model_id 对应的 name
    model_ids = [r.model_id for r in agg_rows]
    name_rows = (
        await db.execute(
            select(ModelConfig.id, ModelConfig.model_name).where(
                ModelConfig.id.in_(model_ids)
            )
        )
    ).all()
    name_by_id = {r.id: r.model_name for r in name_rows}

    # 3) 组装 + 计算 satisfaction_rate + 选 winner
    models: list[ModelMetricRow] = []
    best_rate: float = -1.0
    winner: Optional[str] = None

    for r in agg_rows:
        pos_n = int(r.positive_feedback or 0)
        neg_n = int(r.negative_feedback or 0)
        denom = pos_n + neg_n
        rate = (pos_n / denom) if denom > 0 else 0.0

        # 平均延迟四舍五入成 int
        avg_latency = int(round(float(r.avg_latency_ms or 0)))

        row = ModelMetricRow(
            model_id=r.model_id,
            model_name=name_by_id.get(r.model_id, f"<id={r.model_id}>"),
            total_calls=int(r.total_calls),
            avg_latency_ms=avg_latency,
            total_input_tokens=int(r.total_input_tokens),
            total_output_tokens=int(r.total_output_tokens),
            positive_feedback=pos_n,
            negative_feedback=neg_n,
            satisfaction_rate=rate,
        )
        models.append(row)
        if rate > best_rate:
            best_rate = rate
            winner = row.model_name

    # winner 仅在至少有一行反馈时才有意义；分母全 0 时 best_rate 仍是 0，
    # 但此时 rates 全为 0 不应选 winner
    if best_rate <= 0:
        winner = None

    return MetricsSummaryResponse(
        period_days=days, models=models, winner=winner
    )


# ---------------------------------------------------------------------------
# Connectivity test endpoint
# ---------------------------------------------------------------------------


@router.post(
    "/models/test",
    response_model=ConnectivityTestResponse,
    summary="连通性测试：临时解密 provider key 并发一条 chat",
)
async def test_provider_connectivity(
    body: ConnectivityTestRequest,
    admin_user: dict[str, Any] = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
) -> ConnectivityTestResponse:
    """连通性测试入口。

    成功 → ``{success: True, latency_ms, model}``
    失败 → ``{success: False, error}``（始终 200，绝不 500）
    provider 不存在 → 404（这是显式资源缺失，不算"调用失败"）
    """
    provider = await db.get(ModelProvider, body.provider_id)
    if provider is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Provider {body.provider_id} not found",
        )

    # 1) 解密 key
    try:
        plaintext_key = decrypt_key(provider.api_key_enc)
    except Exception as exc:
        return ConnectivityTestResponse(
            success=False,
            error=f"decrypt api_key failed: {exc}",
        )

    # 2) 构造 client — Mapped[str] 转成 plain str 以匹配 Protocol
    try:
        client = get_client(
            _provider_proxy(provider), plaintext_key
        )
    except Exception as exc:
        return ConnectivityTestResponse(
            success=False,
            error=f"build client failed: {exc}",
        )

    # 3) 发一条 chat 并测延迟
    t0 = time.perf_counter()
    try:
        resp = await client.chat(
            messages=[
                ChatMessage(role="user", content=body.test_message),
            ],
            model=body.model_name,
            temperature=0.7,
            max_tokens=50,
        )
    except Exception as exc:
        # 任何异常 → success=False，绝不 500
        return ConnectivityTestResponse(
            success=False,
            error=f"{type(exc).__name__}: {exc}",
        )

    latency_ms = int((time.perf_counter() - t0) * 1000)
    return ConnectivityTestResponse(
        success=True,
        latency_ms=latency_ms,
        model=body.model_name,
    )
