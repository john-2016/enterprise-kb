"""
Document router — upload, index, list, retrieve, delete, and re-index documents.

All routes require authentication and are prefixed with ``/api/v1/documents``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field
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

# Upload directory — configurable via env or default
UPLOAD_DIR = os.environ.get("DOCUMENT_UPLOAD_DIR", "./data/uploads/")


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
# Helper — inject/store vector store & embedding service (singleton-style)
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
    """Upload a supported document file, extract its text content, and chunk it.

    Supported file types: ``.md``, ``.txt``, ``.pdf``, ``.docx``.
    The document record is persisted to the database immediately.
    """
    # 1. Save the uploaded file to disk
    try:
        file_path = await save_upload_file(file, UPLOAD_DIR)
    except UnsupportedFileTypeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    original_filename = file.filename or "untitled"
    ext = Path(original_filename).suffix.lower().lstrip(".")
    file_type = ext if ext else "txt"
    doc_title = title or Path(original_filename).stem

    # 2. Extract text
    try:
        content_text = extract_text_from_file(file_path, file_type)
    except UnsupportedFileTypeError as exc:
        # Clean up saved file
        Path(file_path).unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    # 3. Chunk the extracted text
    chunks = chunk_text(content_text)
    chunk_count = len(chunks)

    # 4. Insert document record
    owner_id = current_user.get("sub")
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    file_size = Path(file_path).stat().st_size

    result = await db.execute(
        """
        INSERT INTO documents
            (title, filename, file_path, file_size, file_type,
             category, tags, content_text, chunk_count, is_indexed,
             owner_id, created_at, updated_at)
        VALUES
            (:title, :filename, :file_path, :file_size, :file_type,
             :category, :tags, :content_text, :chunk_count, FALSE,
             :owner_id, :created_at, :updated_at)
        RETURNING *
        """,
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
    """Embed all chunks of a document and store them in the vector index.

    Requires that the document exists and has not already been indexed.
    """
    # 1. Fetch document
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

    # 2. Re-chunk (in case chunk_count wasn't set)
    chunks = chunk_text(content_text)

    # 3. Embed all chunks
    embedder = _get_embedder()
    try:
        vectors = await embedder.embed_batch(chunks)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Embedding service error: {exc}",
        ) from exc

    # 4. Build metadata and store in vector index
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

    # 5. Mark document as indexed
    await db.execute(
        "UPDATE documents SET is_indexed = TRUE, chunk_count = :cc, "
        "updated_at = :now WHERE id = :did",
        {
            "cc": len(chunks),
            "now": "datetime('now')",
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
    """Return a paginated list of documents, optionally filtered by category or owner."""
    # Build filter conditions
    filters: list[str] = []
    params: dict[str, Any] = {"skip": skip, "limit": limit}

    if category:
        filters.append("category = :cat")
        params["cat"] = category
    if owner is not None:
        filters.append("owner_id = :oid")
        params["oid"] = owner

    where_clause = " AND ".join(filters) if filters else "TRUE"

    # Count total matching documents
    count_result = await db.execute(
        f"SELECT COUNT(*) FROM documents WHERE {where_clause}",
        params,
    )
    total = count_result.scalar() or 0

    # Fetch paginated results
    result = await db.execute(
        f"SELECT * FROM documents WHERE {where_clause} "
        "ORDER BY created_at DESC OFFSET :skip LIMIT :limit",
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
    """Return full details for a single document by its ID."""
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
    summary="Delete a document",
)
async def delete_doc(
    doc_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete a document record and its associated physical file."""
    try:
        await delete_document(db, str(doc_id))
    except DocumentNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


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
    """Re-extract text from the original file, re-chunk, and re-index.

    This is useful when the document content has changed on disk or when
    upgrading the chunking/embedding strategy.
    """
    # 1. Fetch existing document
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

    # 2. Re-extract text
    try:
        content_text = extract_text_from_file(file_path, file_type)
    except (UnsupportedFileTypeError, ImportError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    # 3. Re-chunk
    chunks = chunk_text(content_text)

    # 4. Update document record with new text and chunk count, reset index flag
    await db.execute(
        "UPDATE documents SET content_text = :ct, chunk_count = :cc, "
        "is_indexed = FALSE, updated_at = :now WHERE id = :did",
        {
            "ct": content_text,
            "cc": len(chunks),
            "now": "datetime('now')",
            "did": doc_id,
        },
    )

    # 5. Re-embed and re-index
    embedder = _get_embedder()
    try:
        vectors = await embedder.embed_batch(chunks)
    except RuntimeError as exc:
        # Don't fail the whole request — text is updated even if indexing fails
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Re-extraction succeeded but embedding failed: {exc}",
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

    # Mark indexed
    await db.execute(
        "UPDATE documents SET is_indexed = TRUE, updated_at = :now WHERE id = :did",
        {"now": "datetime('now')", "did": doc_id},
    )
    await db.commit()

    return {
        "document_id": doc_id,
        "chunks_indexed": len(chunks),
        "message": f"Re-indexed document with {len(chunks)} chunks",
    }
