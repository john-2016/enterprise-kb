"""
Chat router — query the knowledge base with RAG and retrieve query history.

All routes require authentication and are prefixed with ``/api/v1/chat``.
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.core.deps import get_current_user, get_db
from backend.models.ab_test import ABTestMetric
from backend.services.embedding_service import MiniMaxEmbedding, VectorStore
from backend.services.rag_service import RAGService

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])

logger = logging.getLogger(__name__)


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


class QueryResponse(BaseModel):
    answer: str
    sources: list[SourceItem]
    tokens_used: int


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
) -> dict[str, Any]:
    embedder, store, rag = _get_services()

    try:
        query_vector = await embedder.embed_text(body.question)
    except RuntimeError:
        logger.exception("Embedding service error")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Upstream embedding service temporarily unavailable",
        )

    results = store.search(query_vector, top_k=body.top_k)

    sources: list[dict[str, Any]] = []
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

    try:
        answer = await rag.query(body.question, top_k=body.top_k)
    except Exception:
        logger.exception("RAG query failed")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Upstream RAG service temporarily unavailable",
        )

    tokens_used = _estimate_tokens(body.question) + _estimate_tokens(answer)

    user_id = int(current_user.get("sub"))
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
                "details": f"question={body.question[:200]!r}, top_k={body.top_k}",
                "now": datetime.now(timezone.utc),
            },
        )
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("Audit log write failed")

    return {
        "answer": answer,
        "sources": [s.model_dump() for s in sources],
        "tokens_used": tokens_used,
    }


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
