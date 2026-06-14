"""
Chat router — query the knowledge base with RAG and retrieve query history.

All routes require authentication and are prefixed with ``/api/v1/chat``.
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.core.crypto import decrypt_key
from backend.core.deps import get_current_user, get_db
from backend.core.errors import AllModelsFailedError
from backend.models.ab_test import ABTestMetric, ABTestRule
from backend.models.model_config import ModelConfig
from backend.models.provider import ModelProvider
from backend.services.embedding_service import MiniMaxEmbedding, VectorStore
from backend.services.model_clients.base import ChatMessage
from backend.services.model_router import ModelRouter
from backend.services.rag_service import RAGService, _DEFAULT_RAG_PROMPT, _DEFAULT_SYSTEM_PROMPT

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------
# ModelRouter 用的 client 工厂（生产路径）
# -----------------------------------------------------------------------

def _default_router_client(provider, decrypted_key: str):
    """从 ModelProvider ORM 实例构造 UnifiedModelClient。"""
    from backend.services.model_clients.factory import get_client
    # 构造 provider-like object（factory 只需 provider_type + api_base_url）
    from types import SimpleNamespace
    proxy = SimpleNamespace(
        provider_type=provider.provider_type,
        api_base_url=provider.api_base_url,
    )
    return get_client(proxy, decrypted_key)


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class SourceItem(BaseModel):
    doc_id: str
    title: str
    chunk_index: int
    score: float
    snippet: str


class QueryRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=4000)
    kb_id: int | None = Field(None, description="Optional knowledge-base ID filter")
    top_k: int = Field(default=5, ge=1, le=50)


class ModelUsedInfo(BaseModel):
    """实际选用的模型信息（多模型支持后追加）。"""

    id: int | None = None
    name: str | None = None
    provider: str | None = None


class TokenUsage(BaseModel):
    """分项 token 计数。"""

    input: int = 0
    output: int = 0


class QueryResponse(BaseModel):
    answer: str
    sources: list[SourceItem]
    tokens_used: int
    # 下面字段是 multi-model 改造后追加的（v1 客户端不感知）
    model_used: ModelUsedInfo | None = None
    latency_ms: int | None = None
    tokens: TokenUsage | None = None


class HistoryItem(BaseModel):
    id: int
    question: str
    answer_preview: str | None = None
    top_k: int
    retrieved_document_ids: list[str] = []
    created_at: str


class HistoryResponse(BaseModel):
    items: list[HistoryItem]
    total: int


class FeedbackRequest(BaseModel):
    metric_id: int = Field(..., description="ABTestMetric 主键")
    feedback: int = Field(..., ge=-1, le=1, description="-1=👎, 0=中性, 1=👍")
    feedback_text: str | None = Field(None, max_length=2000)


class FeedbackResponse(BaseModel):
    success: bool
    metric_id: int


# ---------------------------------------------------------------------------
# Singleton instances (lazily created)
# ---------------------------------------------------------------------------

_embedder: MiniMaxEmbedding | None = None
_vector_store: VectorStore | None = None
_rag_service: RAGService | None = None


def _get_services() -> tuple[MiniMaxEmbedding, VectorStore, RAGService]:
    global _embedder, _vector_store, _rag_service

    if _embedder is None:
        _embedder = MiniMaxEmbedding()
    if _vector_store is None:
        _vector_store = VectorStore(dimension=settings.VECTOR_DIMENSION)
    if _rag_service is None:
        _rag_service = RAGService(
            embedding_service=_embedder,
            vector_store=_vector_store,
        )

    return _embedder, _vector_store, _rag_service


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _estimate_tokens(text_: str) -> int:
    return max(1, len(text_) // 4)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/query",
    response_model=QueryResponse,
    summary="Ask a question against the knowledge base",
)
async def query_knowledge_base(
    body: QueryRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> QueryResponse:
    """RAG 查询：保留 v1 行为（embed → retrieve → chat），
    但 chat 步骤改走 ModelRouter 以支持多模型 + A/B + 落 ABTestMetric。
    """
    embedder, store, _rag = _get_services()  # 不用 rag.query，改用 router

    # ---------- 1) 检索：保留 v1 行为，build sources ----------
    try:
        query_vector = await embedder.embed_text(body.question)
    except RuntimeError:
        logger.exception("Embedding service error")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Upstream embedding service temporarily unavailable",
        )

    results = store.search(query_vector, top_k=body.top_k)
    sources: list[SourceItem] = []
    for doc_id, score, meta in results:
        chunk_text_ = meta.get("text", meta.get("content", ""))
        sources.append(
            SourceItem(
                doc_id=str(doc_id),
                title=meta.get("title", meta.get("filename", str(doc_id))),
                chunk_index=meta.get("chunk_index", 0),
                score=float(score),
                snippet=chunk_text_[:300],
            )
        )

    # ---------- 2) 加载多模型数据：providers + configs + ab rules ----------
    user_id = int(current_user.get("sub"))

    providers_result = await db.execute(select(ModelProvider).where(ModelProvider.enabled == True))
    providers = {p.id: p for p in providers_result.scalars().all()}

    models_result = await db.execute(select(ModelConfig).where(ModelConfig.enabled == True))
    models = list(models_result.scalars().all())

    rules_result = await db.execute(
        select(ABTestRule).where(
            ABTestRule.target == "chat", ABTestRule.enabled == True
        )
    )
    rules = list(rules_result.scalars().all())

    # 解密每个 provider 的 key（解密失败时给空串，让 FallbackChain 跳过）
    default_keys: dict[int, str] = {}
    for p in providers.values():
        try:
            default_keys[p.id] = decrypt_key(p.api_key_enc)
        except Exception:
            logger.warning("provider %s decrypt failed, will be skipped", p.name)
            default_keys[p.id] = ""

    # 关联 model.provider — SQLAlchemy relationship 已自动 lazy load
    for m in models:
        _ = m.provider  # 触发 lazy load，确保 router 能拿到

    # ---------- 3) 构造 RAG messages（与 rag_service 一致） ----------
    context_parts: list[str] = []
    for idx, (doc_id, score, meta) in enumerate(results, 1):
        chunk_text = meta.get("text", meta.get("content", ""))
        source_name = meta.get("filename", meta.get("source", doc_id))
        context_parts.append(
            f"[{idx}] (source: {source_name}, relevance: {score:.4f})\n{chunk_text}"
        )
    context = "\n\n---\n\n".join(context_parts) if context_parts else (
        "No relevant documents were found in the knowledge base."
    )
    system_text = _DEFAULT_SYSTEM_PROMPT.format(context=context)
    user_text = _DEFAULT_RAG_PROMPT.format(question=body.question)
    messages = [
        ChatMessage(role="system", content=system_text),
        ChatMessage(role="user", content=user_text),
    ]

    # ---------- 4) 调 ModelRouter.chat ----------
    router = ModelRouter(
        ab_rules=rules,
        all_models=models,
        get_client_fn=_default_router_client,
        default_keys=default_keys,
    )

    t0 = time.perf_counter()
    try:
        chat_resp = await router.chat(
            user_id=user_id, target="chat", messages=messages,
            temperature=0.7, max_tokens=2048,
        )
        latency_ms = int((time.perf_counter() - t0) * 1000)
    except AllModelsFailedError as exc:
        logger.exception("All models failed for user %s", user_id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"All models failed: {exc}",
        )

    # 解析实际用的 model（用于响应和 metric）
    try:
        primary_model = router._resolve(user_id, "chat")
        provider_name = primary_model.provider.name if primary_model.provider else "unknown"
    except Exception:
        primary_model = None
        provider_name = "unknown"

    # ---------- 5) 落 ABTestMetric ----------
    try:
        rule_id = rules[0].id if rules else None
        if primary_model is not None:
            metric = ABTestMetric(
                user_id=user_id,
                model_id=primary_model.id,
                ab_rule_id=rule_id,
                request_type="chat",
                latency_ms=latency_ms,
                input_tokens=chat_resp.input_tokens,
                output_tokens=chat_resp.output_tokens,
            )
            db.add(metric)
            await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("ABTestMetric write failed (non-fatal)")

    # ---------- 6) 审计日志（保留 v1 行为） ----------
    try:
        await db.execute(
            text("""
            INSERT INTO audit_logs
                (user_id, action, target_type, target_id, details, ip_address, created_at)
            VALUES
                (:uid, 'query', 'knowledge_base', :kb_id, :details, NULL, :now)
            """),
            {
                "uid": user_id,
                "kb_id": body.kb_id,
                "details": f"question={body.question[:200]!r}, top_k={body.top_k}, model={provider_name}",
                "now": datetime.now(timezone.utc),
            },
        )
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("Audit log write failed")

    # ---------- 7) 响应：v1 字段保留 + 追加 multi-model 字段 ----------
    return QueryResponse(
        answer=chat_resp.content,
        sources=sources,
        tokens_used=chat_resp.input_tokens + chat_resp.output_tokens,
        model_used=ModelUsedInfo(
            id=primary_model.id if primary_model else None,
            name=primary_model.model_name if primary_model else None,
            provider=provider_name,
        ),
        latency_ms=latency_ms,
        tokens=TokenUsage(
            input=chat_resp.input_tokens,
            output=chat_resp.output_tokens,
        ),
    )


@router.get(
    "/history",
    response_model=HistoryResponse,
    summary="Get recent query history",
)
async def query_history(
    skip: int = Query(0, ge=0, description="Records to skip"),
    limit: int = Query(20, ge=1, le=200, description="Max records to return"),
    current_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    user_id = int(current_user.get("sub"))

    count_result = await db.execute(
        text("SELECT COUNT(*) FROM audit_logs "
             "WHERE user_id = :uid AND action = 'query'"),
        {"uid": user_id},
    )
    total = count_result.scalar() or 0

    result = await db.execute(
        text("SELECT * FROM audit_logs "
             "WHERE user_id = :uid AND action = 'query' "
             "ORDER BY created_at DESC OFFSET :skip LIMIT :limit"),
        {"uid": user_id, "skip": skip, "limit": limit},
    )
    rows = result.mappings().all()

    items: list[dict[str, Any]] = []
    for row in rows:
        details = row.get("details") or ""
        question = details
        if "question=" in details:
            try:
                question = details.split("question=", 1)[1].split(", top_k=")[0]
                question = question.strip("'\"")
            except (IndexError, ValueError):
                question = details

        items.append(
            HistoryItem(
                id=row["id"],
                question=question,
                answer_preview=None,
                top_k=5,
                retrieved_document_ids=[],
                created_at=str(row.get("created_at", "")),
            )
        )

    return {"items": items, "total": total}


# ---------------------------------------------------------------------------
# Feedback (Task 4.5)
# ---------------------------------------------------------------------------


@router.post(
    "/feedback",
    response_model=FeedbackResponse,
    summary="Submit feedback (-1/0/1) on a previous chat",
)
async def submit_feedback(
    body: FeedbackRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """登录用户对自己产生过的 ABTestMetric 提交反馈。

    鉴权：任意已登录用户（不要求 admin）。
    所有权校验：``metric.user_id`` 必须等于当前 user.id；否则 403。
    """
    user_id = int(current_user.get("sub"))

    # 1. 查 metric
    res = await db.execute(
        select(ABTestMetric).where(ABTestMetric.id == body.metric_id)
    )
    metric = res.scalar_one_or_none()
    if metric is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"metric {body.metric_id} not found",
        )

    # 2. 所有权校验
    if metric.user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot give feedback on another user's metric",
        )

    # 3. 写入反馈
    metric.feedback = body.feedback
    metric.feedback_text = body.feedback_text
    await db.commit()

    return {"success": True, "metric_id": metric.id}
