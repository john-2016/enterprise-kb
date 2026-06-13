"""Document upload, text extraction, chunking, and CRUD service.

Supported file types: ``.md``, ``.txt``, ``.pdf``, ``.docx``.
"""

from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import UploadFile
from sqlalchemy import text

# ---------------------------------------------------------------------------
# Optional parser imports
# ---------------------------------------------------------------------------

try:
    import pypdf  # pip install pypdf
except ImportError:
    pypdf = None

try:
    import docx  # pip install python-docx
except ImportError:
    docx = None


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class DocumentNotFoundError(Exception):
    """Raised when a document is not found."""


class UnsupportedFileTypeError(Exception):
    """Raised when the uploaded file type is not supported."""


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SUPPORTED_EXTENSIONS = {".md", ".txt", ".pdf", ".docx"}


# ---------------------------------------------------------------------------
# 1. File upload
# ---------------------------------------------------------------------------

async def save_upload_file(
    upload_file: UploadFile,
    upload_dir: str | Path,
) -> str:
    """Save an uploaded file to *upload_dir* and return the absolute path.

    The file is saved under a UUID-based name to avoid collisions while
    preserving the original extension.
    """
    upload_dir = Path(upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)

    ext = Path(upload_file.filename or "").suffix.lower() if upload_file.filename else ""
    if ext not in SUPPORTED_EXTENSIONS:
        raise UnsupportedFileTypeError(
            f"Unsupported file type '{ext}'. "
            f"Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
        )

    safe_name = f"{uuid.uuid4().hex}{ext}"
    dest = upload_dir / safe_name

    content = await upload_file.read()
    dest.write_bytes(content)

    return str(dest.resolve())


# ---------------------------------------------------------------------------
# 2. Text extraction
# ---------------------------------------------------------------------------

def _extract_md(file_path: str) -> str:
    return Path(file_path).read_text(encoding="utf-8")


def _extract_txt(file_path: str) -> str:
    return Path(file_path).read_text(encoding="utf-8")


def _extract_pdf(file_path: str) -> str:
    if pypdf is None:
        raise ImportError("pypdf is required to extract PDF files. "
                          "Install with: pip install pypdf")
    reader = pypdf.PdfReader(file_path)
    pages = []
    for page in reader.pages:
        text_ = page.extract_text()
        if text_:
            pages.append(text_)
    return "\n\n".join(pages)


def _extract_docx(file_path: str) -> str:
    if docx is None:
        raise ImportError("python-docx is required to extract .docx files. "
                          "Install with: pip install python-docx")
    document = docx.Document(file_path)
    paragraphs = [p.text for p in document.paragraphs if p.text.strip()]
    return "\n\n".join(paragraphs)


_EXTRACTORS = {
    ".md": _extract_md,
    ".txt": _extract_txt,
    ".pdf": _extract_pdf,
    ".docx": _extract_docx,
}


def extract_text_from_file(file_path: str, file_type: str) -> str:
    ext = f".{file_type.lstrip('.').lower()}"
    extractor = _EXTRACTORS.get(ext)
    if extractor is None:
        raise UnsupportedFileTypeError(
            f"Unsupported file type '{ext}'. "
            f"Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
        )
    return extractor(file_path)


# ---------------------------------------------------------------------------
# 3. Text chunking
# ---------------------------------------------------------------------------

def chunk_text(
    text_: str,
    chunk_size: int = 512,
    overlap: int = 128,
) -> list[str]:
    """Split *text* into overlapping chunks of approximately *chunk_size*
    characters each, respecting sentence/paragraph boundaries where possible."""
    if not text_:
        return []

    if overlap >= chunk_size:
        raise ValueError("overlap must be smaller than chunk_size")

    text_ = re.sub(r"\s+", " ", text_).strip()

    chunks: list[str] = []
    start = 0
    text_len = len(text_)
    last_start = start

    while start < text_len:
        end = min(start + chunk_size, text_len)

        if end < text_len:
            search_start = max(start, end - chunk_size // 4)
            candidate = text_.rfind(". ", search_start, end)
            if candidate == -1:
                candidate = text_.rfind("! ", search_start, end)
            if candidate == -1:
                candidate = text_.rfind("? ", search_start, end)
            if candidate == -1:
                candidate = text_.rfind("\n", search_start, end)
            if candidate != -1:
                end = candidate + 1

        chunks.append(text_[start:end].strip())

        step = max(chunk_size - overlap, 1)
        next_start = end - overlap if end - overlap > start else start + step

        if next_start <= last_start:
            next_start = end
        last_start = next_start
        start = next_start

    return [c for c in chunks if c]


# ---------------------------------------------------------------------------
# 4. Document CRUD
# ---------------------------------------------------------------------------

def _make_document(
    doc_id: str,
    filename: str,
    file_path: str,
    file_type: str,
    owner_id: str,
    chunk_count: int = 0,
) -> dict:
    return {
        "id": doc_id,
        "filename": filename,
        "file_path": file_path,
        "file_type": file_type,
        "owner_id": owner_id,
        "chunk_count": chunk_count,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }


async def get_document(db, doc_id: str) -> dict:
    result = await db.execute(
        text("SELECT * FROM documents WHERE id = :did"),
        {"did": int(doc_id)},
    )
    doc = result.mappings().first()
    if doc is None:
        raise DocumentNotFoundError(f"Document {doc_id} not found")
    return dict(doc)


async def list_documents(
    db,
    skip: int = 0,
    limit: int = 100,
    owner_id: Optional[str] = None,
) -> list[dict]:
    if owner_id:
        result = await db.execute(
            text("SELECT * FROM documents WHERE owner_id = :oid "
                 "ORDER BY created_at DESC OFFSET :skip LIMIT :limit"),
            {"oid": owner_id, "skip": skip, "limit": limit},
        )
    else:
        result = await db.execute(
            text("SELECT * FROM documents ORDER BY created_at DESC "
                 "OFFSET :skip LIMIT :limit"),
            {"skip": skip, "limit": limit},
        )
    rows = result.mappings().all()
    return [dict(r) for r in rows]


async def delete_document(db, doc_id: str) -> None:
    doc = await get_document(db, doc_id)

    await db.execute(
        text("DELETE FROM documents WHERE id = :did"),
        {"did": int(doc_id)},
    )
    await db.commit()

    file_path = doc.get("file_path")
    if file_path and Path(file_path).exists():
        Path(file_path).unlink(missing_ok=True)
