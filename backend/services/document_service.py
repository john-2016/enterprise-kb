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

# ---------------------------------------------------------------------------
# Optional parser imports — gracefully absent when dependencies are missing
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

    # Extract extension and validate
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
    """Read a plain markdown (or text) file."""
    return Path(file_path).read_text(encoding="utf-8")


def _extract_txt(file_path: str) -> str:
    return Path(file_path).read_text(encoding="utf-8")


def _extract_pdf(file_path: str) -> str:
    """Extract text from a PDF via pypdf."""
    if pypdf is None:
        raise ImportError("pypdf is required to extract PDF files. "
                          "Install with: pip install pypdf")
    reader = pypdf.PdfReader(file_path)
    pages = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            pages.append(text)
    return "\n\n".join(pages)


def _extract_docx(file_path: str) -> str:
    """Extract text from a .docx file via python-docx."""
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
    """Extract plain text from a file based on its type.

    Parameters
    ----------
    file_path : str
        Absolute path to the file on disk.
    file_type : str
        One of ``"md"``, ``"txt"``, ``"pdf"``, ``"docx"``.

    Returns
    -------
    str
        Extracted text content.
    """
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
    text: str,
    chunk_size: int = 512,
    overlap: int = 128,
) -> list[str]:
    """Split *text* into overlapping chunks of approximately *chunk_size*
    characters each, respecting sentence/paragraph boundaries where possible.

    Parameters
    ----------
    text : str
        The full document text.
    chunk_size : int
        Target chunk length in characters (default 512).
    overlap : int
        Number of overlapping characters between consecutive chunks
        (default 128).

    Returns
    -------
    list[str]
        Ordered list of text chunks.
    """
    if not text:
        return []

    if overlap >= chunk_size:
        raise ValueError("overlap must be smaller than chunk_size")

    # Normalise whitespace
    text = re.sub(r"\s+", " ", text).strip()

    chunks: list[str] = []
    start = 0
    text_len = len(text)

    last_start = start

    while start < text_len:
        end = min(start + chunk_size, text_len)

        # Try to break at a sentence boundary near the end of the chunk
        if end < text_len:
            # Look backward for sentence-ending punctuation
            search_start = max(start, end - chunk_size // 4)
            candidate = text.rfind(". ", search_start, end)
            if candidate == -1:
                candidate = text.rfind("! ", search_start, end)
            if candidate == -1:
                candidate = text.rfind("? ", search_start, end)
            if candidate == -1:
                candidate = text.rfind("\n", search_start, end)
            if candidate != -1:
                end = candidate + 1  # include the punctuation

        chunks.append(text[start:end].strip())

        # Advance by step, ensuring we make forward progress
        step = max(chunk_size - overlap, 1)
        next_start = end - overlap if end - overlap > start else start + step

        # Safety: if we somehow didn't advance, force forward
        if next_start <= last_start:
            next_start = end
        last_start = next_start
        start = next_start

    # Drop any empty trailing chunk
    return [c for c in chunks if c]


# ---------------------------------------------------------------------------
# 4. Document CRUD (stub — adapt to your ORM / DB layer)
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
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


async def get_document(db, doc_id: str) -> dict:
    """Retrieve a single document record.

    Raises
    ------
    DocumentNotFoundError
    """
    result = await db.execute(
        "SELECT * FROM documents WHERE id = :did",
        {"did": doc_id},
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
    """List documents with optional owner filter and pagination."""
    if owner_id:
        result = await db.execute(
            "SELECT * FROM documents WHERE owner_id = :oid "
            "ORDER BY created_at DESC OFFSET :skip LIMIT :limit",
            {"oid": owner_id, "skip": skip, "limit": limit},
        )
    else:
        result = await db.execute(
            "SELECT * FROM documents ORDER BY created_at DESC "
            "OFFSET :skip LIMIT :limit",
            {"skip": skip, "limit": limit},
        )
    rows = result.mappings().all()
    return [dict(r) for r in rows]


async def delete_document(db, doc_id: str) -> None:
    """Delete a document record and its physical file.

    Raises
    ------
    DocumentNotFoundError
    """
    # Fetch so we can clean up the file
    doc = await get_document(db, doc_id)

    await db.execute(
        "DELETE FROM documents WHERE id = :did",
        {"did": doc_id},
    )
    await db.commit()

    # Remove the physical file if it still exists
    file_path = doc.get("file_path")
    if file_path and Path(file_path).exists():
        Path(file_path).unlink(missing_ok=True)
