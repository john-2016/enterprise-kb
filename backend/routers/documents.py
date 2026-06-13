"""
Document router — upload, index, list, retrieve, delete, and re-index documents.

All routes require authentication and are prefixed with ``/api/v1/documents``.
"""

from __future__ import annotations

import os
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.deps import get_current_user, get_db
from backend.services.document_service import (
    DocumentNotFoundError,
    UnsupportedFileTypeError,
    chunk_text,
    delete_document,
    extract_text_from_file,
    get_document,
    list_documents,
    save_upload_file,
)
from backend.services.embedding_service import MiniMaxEmbedding, VectorStore
from backend.config import settings

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])

logger = logging.getLogger(__name__)

UPLOAD_DIR = os.environ.get("DOCUMENT_UPLOAD_DIR", "./data/uploads/")


def _sanitize_filename(s: str) -> str:
    """剥离控制字符、HTML 特殊字符，截断 200 字符（H6）。"""
    import re
    s = re.sub(r"[\x00-\x1f\x7f<>:\"\\|?*]", "", s).strip()
    if not s:
        s = "untitled"
    return s[:200]


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class DocumentResponse(BaseModel):
    id: int
    title: str
    filename: str
    file_type: str
    file_size: int
    category: str | None = None
    tags: str | None = None
    chunk_count: int = 0
    is_indexed: bool = False
    owner_id: int
    created_at: str
    updated_at: str
    content_text: str | None = None


class DocumentListResponse(BaseModel):
    items: list[DocumentResponse]
    total: int
    skip: int
    limit: int


class IndexResponse(BaseModel):
    document_id: int
    chunks_indexed: int
    message: str


class ReindexResponse(BaseModel):
    document_id: int
    chunks_indexed: int
    message: str


class UploadResponse(BaseModel):
    document: DocumentResponse
    chunks_created: int
    message: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_embedder() -> MiniMaxEmbedding:
    return MiniMaxEmbedding()


def _get_vector_store() -> VectorStore:
    return VectorStore(dimension=settings.VECTOR_DIMENSION)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/upload",
    status_code=status.HTTP_201_CREATED,
    response_model=UploadResponse,
    summary="Upload a file, extract text, and chunk it",
)
async def upload_document(
    file: UploadFile,
    title: str | None = Query(None, description="Optional display title"),
    category: str | None = Query(None, description="Document category"),
    tags: str | None = Query(None, description="Comma-separated tags"),
    current_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        file_path = await save_upload_file(file, UPLOAD_DIR)
    except UnsupportedFileTypeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        # 文件超过 50MB（H5）
        if "exceeds maximum size" in str(exc):
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=str(exc),
            ) from exc
        raise

    original_filename = _sanitize_filename(file.filename or "untitled")
    title = _sanitize_filename(title) if title else None
    ext = Path(original_filename).suffix.lower().lstrip(".")
    file_type = ext if ext else "txt"
    doc_title = title or Path(original_filename).stem

    try:
        content_text = extract_text_from_file(file_path, file_type)
    except UnsupportedFileTypeError as exc:
        Path(file_path).unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    chunks = chunk_text(content_text)
    chunk_count = len(chunks)

    owner_id = current_user.get("sub")
    now = datetime.now(timezone.utc)
    file_size = Path(file_path).stat().st_size

    result = await db.execute(
        text("""
        INSERT INTO documents
            (title, filename, file_path, file_size, file_type,
             category, tags, content_text, chunk_count, is_indexed,
             owner_id, created_at, updated_at)
        VALUES
            (:title, :filename, :file_path, :file_size, :file_type,
             :category, :tags, :content_text, :chunk_count, FALSE,
             :owner_id, :created_at, :updated_at)
        RETURNING *
        """),
        {
            "title": doc_title,
            "filename": original_filename,
            "file_path": file_path,
            "file_size": file_size,
            "file_type": file_type,
            "category": category,
            "tags": tags,
            "content_text": content_text,
            "chunk_count": chunk_count,
            "owner_id": owner_id,
            "created_at": now,
            "updated_at": now,
        },
    )
    await db.commit()
    doc_row = result.mappings().first()

    return {
        "document": dict(doc_row),
        "chunks_created": chunk_count,
        "message": f"Document uploaded with {chunk_count} chunks created",
    }


@router.post(
    "/{doc_id}/index",
    response_model=IndexResponse,
    summary="Embed chunks and save to vector store",
)
async def index_document(
    doc_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        doc = await get_document(db, str(doc_id))
    except DocumentNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    if doc.get("is_indexed"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Document is already indexed. Use re-index to rebuild.",
        )

    content_text = doc.get("content_text", "")
    if not content_text:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Document has no extracted text content to index",
        )

    chunks = chunk_text(content_text)

    embedder = _get_embedder()
    try:
        vectors = await embedder.embed_batch(chunks)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Embedding service error: {exc}",
        ) from exc

    store = _get_vector_store()
    chunk_ids = [f"doc_{doc_id}_chunk_{i}" for i in range(len(chunks))]
    metadata_list = [
        {
            "doc_id": str(doc_id),
            "title": doc.get("title", ""),
            "filename": doc.get("filename", ""),
            "chunk_index": i,
            "text": chunk,
        }
        for i, chunk in enumerate(chunks)
    ]

    store.add_vectors(chunk_ids, vectors, metadata_list)

    await db.execute(
        text("UPDATE documents SET is_indexed = TRUE, chunk_count = :cc, "
             "updated_at = :now WHERE id = :did"),
        {
            "cc": len(chunks),
            "now": datetime.now(timezone.utc),
            "did": doc_id,
        },
    )
    await db.commit()

    return {
        "document_id": doc_id,
        "chunks_indexed": len(chunks),
        "message": f"Successfully indexed {len(chunks)} chunks",
    }


@router.get(
    "/",
    response_model=DocumentListResponse,
    summary="List documents (paginated, filterable)",
)
async def list_docs(
    skip: int = Query(0, ge=0, description="Records to skip"),
    limit: int = Query(100, ge=1, le=500, description="Max records to return"),
    category: str | None = Query(None, description="Filter by category"),
    owner: int | None = Query(
        None, alias="owner_id", description="Filter by owner user ID"
    ),
    current_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    filters: list[str] = []
    params: dict[str, Any] = {"skip": skip, "limit": limit}

    if category:
        filters.append("category = :cat")
        params["cat"] = category
    if owner is not None:
        filters.append("owner_id = :oid")
        params["oid"] = owner

    where_clause = " AND ".join(filters) if filters else "TRUE"

    count_result = await db.execute(
        text(f"SELECT COUNT(*) FROM documents WHERE {where_clause}"),
        params,
    )
    total = count_result.scalar() or 0

    result = await db.execute(
        text(f"SELECT * FROM documents WHERE {where_clause} "
             "ORDER BY created_at DESC OFFSET :skip LIMIT :limit"),
        params,
    )
    rows = result.mappings().all()

    return {
        "items": [dict(r) for r in rows],
        "total": total,
        "skip": skip,
        "limit": limit,
    }


@router.get(
    "/{doc_id}",
    response_model=DocumentResponse,
    summary="Get document details",
)
async def get_doc(
    doc_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        doc = await get_document(db, str(doc_id))
    except DocumentNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    return dict(doc)


@router.delete(
    "/{doc_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    summary="Delete a document",
)
async def delete_doc(
    doc_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    try:
        await delete_document(db, str(doc_id))
    except DocumentNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    # H7: 同步清理向量库（按 doc_<id>_chunk_ 前缀）
    try:
        store = _get_vector_store()
        store.remove_ids_with_prefix(f"doc_{doc_id}_chunk_")
    except Exception:
        logger.exception("vector store cleanup failed for doc %s", doc_id)


@router.post(
    "/{doc_id}/reindex",
    response_model=ReindexResponse,
    summary="Re-extract text and re-index a document",
)
async def reindex_document(
    doc_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        doc = await get_document(db, str(doc_id))
    except DocumentNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    file_path = doc.get("file_path")
    file_type = doc.get("file_type")

    if not file_path or not Path(file_path).exists():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Original file no longer exists on disk",
        )

    try:
        content_text = extract_text_from_file(file_path, file_type)
    except (UnsupportedFileTypeError, ImportError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    chunks = chunk_text(content_text)

    await db.execute(
        text("UPDATE documents SET content_text = :ct, chunk_count = :cc, "
             "is_indexed = FALSE, updated_at = :now WHERE id = :did"),
        {
            "ct": content_text,
            "cc": len(chunks),
            "now": datetime.now(timezone.utc),
            "did": doc_id,
        },
    )

    embedder = _get_embedder()
    try:
        vectors = await embedder.embed_batch(chunks)
    except RuntimeError:
        # 显式回滚：不让"已更新 content_text、is_indexed=FALSE"持久化
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Re-extraction succeeded but embedding failed; rolled back",
        )

    store = _get_vector_store()
    chunk_ids = [f"doc_{doc_id}_chunk_{i}" for i in range(len(chunks))]
    metadata_list = [
        {
            "doc_id": str(doc_id),
            "title": doc.get("title", ""),
            "filename": doc.get("filename", ""),
            "chunk_index": i,
            "text": chunk,
        }
        for i, chunk in enumerate(chunks)
    ]

    store.add_vectors(chunk_ids, vectors, metadata_list)

    await db.execute(
        text("UPDATE documents SET is_indexed = TRUE, updated_at = :now WHERE id = :did"),
        {"now": datetime.now(timezone.utc), "did": doc_id},
    )
    await db.commit()

    return {
        "document_id": doc_id,
        "chunks_indexed": len(chunks),
        "message": f"Re-indexed document with {len(chunks)} chunks",
    }
