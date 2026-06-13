"""Embedding and vector-store service.

Two primary classes:

* **MiniMaxEmbedding** — calls the MiniMax ``embo-01`` API via ``httpx``.
* **VectorStore** — in-memory FAISS index backed by ``numpy`` for
  approximate nearest-neighbour search with metadata tracking.
"""

from __future__ import annotations

import json
import os
import pickle
from pathlib import Path
from typing import Any, Optional

import numpy as np

# ---------------------------------------------------------------------------
# Optional dependencies
# ---------------------------------------------------------------------------

try:
    import faiss
except ImportError:
    faiss = None  # type: ignore[assignment]

try:
    import httpx
except ImportError:
    httpx = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_EMBO_MODEL = "embo-01"
DEFAULT_CHAT_MODEL = "MiniMax-M3"  # used by RAG service
MINIMAX_BASE_URL = "https://api.minimaxi.com"
MINIMAX_EMBED_URL = f"{MINIMAX_BASE_URL}/v1/embeddings"
MINIMAX_CHAT_URL = f"{MINIMAX_BASE_URL}/v1/chat/completions"


# ===================================================================
# MiniMaxEmbedding
# ===================================================================

class MiniMaxEmbedding:
    """Client for the MiniMax ``embo-01`` embedding API.

    Reads the API key from the ``MINIMAX_API_KEY`` environment variable.
    Uses ``httpx`` for async HTTP calls.
    """

    def __init__(self) -> None:
        self.api_key: str | None = (
            os.environ.get("MINIMAX_API_KEY") or os.environ.get("MINIMAX_CN_API_KEY")
        )
        self.model: str = DEFAULT_EMBO_MODEL
        self._client: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------
    # Client lifecycle
    # ------------------------------------------------------------------

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            if httpx is None:
                raise ImportError("httpx is required. Install with: pip install httpx")
            self._client = httpx.AsyncClient(timeout=60.0)
        return self._client

    async def _ensure_authenticated(self) -> None:
        if not self.api_key:
            raise RuntimeError(
                "MINIMAX_API_KEY environment variable is not set. "
                "Please set it before using MiniMaxEmbedding."
            )

    async def close(self) -> None:
        """Release the underlying HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def embed_text(self, text: str) -> list[float]:
        """Embed a single text string.

        Returns a 1536-dimensional vector (``embo-01`` default).
        """
        await self._ensure_authenticated()
        client = await self._get_client()

        payload = {
            "model": self.model,
            "texts": [text],
            "type": "db"
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        resp = await client.post(MINIMAX_EMBED_URL, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()

        # MiniMax CN returns: {"vectors": [[...]], "total_tokens": N}
        vectors = data.get("vectors", [])
        if vectors:
            return vectors[0]
        raise ValueError(f"Unexpected MiniMax embedding response: {data}")

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts in a single API call.

        Parameters
        ----------
        texts : list[str]
            List of text strings to embed.

        Returns
        -------
        list[list[float]]
            One 1536-d vector per input text.
        """
        if not texts:
            return []

        await self._ensure_authenticated()
        client = await self._get_client()

        payload = {
            "model": self.model,
            "texts": texts,
            "type": "db"
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        resp = await client.post(MINIMAX_EMBED_URL, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()

        # MiniMax CN returns: {"vectors": [[...], [...], ...], "total_tokens": N}
        vectors = data.get("vectors", [])
        if vectors:
            return vectors
        raise ValueError(f"Unexpected MiniMax batch embedding response: {data}")


# ===================================================================
# VectorStore
# ===================================================================

class VectorStore:
    """In-memory vector store backed by a FAISS index with metadata.

    Provides approximate nearest-neighbour search, save/load to disk, and
    metadata tracking alongside vector ids.
    """

    def __init__(self, dimension: int = 1536) -> None:
        if faiss is None:
            raise ImportError(
                "faiss is required. Install with: pip install faiss-cpu (or faiss-gpu)"
            )

        self.dimension = dimension
        self.index: faiss.Index = faiss.IndexFlatL2(dimension)
        self._id_map: dict[str, int] = {}          # external_id -> faiss internal pos
        self._reverse_map: dict[int, str] = {}      # faiss internal pos -> external_id
        self._metadata: dict[str, dict[str, Any]] = {}  # external_id -> metadata
        self._next_pos: int = 0

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def add_vectors(
        self,
        ids: list[str],
        vectors: list[list[float]],
        metadata: list[dict[str, Any]] | None = None,
    ) -> None:
        """Add vectors to the index.

        Parameters
        ----------
        ids : list[str]
            Unique external identifiers (one per vector).
        vectors : list[list[float]]
            Dense vectors matching ``self.dimension``.
        metadata : list[dict] | None
            Optional per-vector metadata dicts.
        """
        if len(ids) != len(vectors):
            raise ValueError("ids and vectors must have the same length")

        if metadata is not None and len(metadata) != len(ids):
            raise ValueError("metadata length must match ids length")

        mat = np.array(vectors, dtype=np.float32)
        if mat.shape[1] != self.dimension:
            raise ValueError(
                f"Expected vectors of dimension {self.dimension}, "
                f"got {mat.shape[1]}"
            )

        # FAISS internal add
        self.index.add(mat)

        # Track mappings
        n = len(ids)
        meta_list = metadata if metadata else [{} for _ in range(n)]
        for i, ext_id in enumerate(ids):
            self._id_map[ext_id] = self._next_pos
            self._reverse_map[self._next_pos] = ext_id
            self._metadata[ext_id] = meta_list[i]
            self._next_pos += 1

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(
        self,
        query_vector: list[float],
        top_k: int = 5,
    ) -> list[tuple[str, float, dict[str, Any]]]:
        """Search the index for the *top_k* nearest neighbours.

        Returns
        -------
        list[tuple[str, float, dict]]
            Each tuple is ``(external_id, l2_distance, metadata)``, ordered
            by increasing distance (most similar first).
        """
        if self.index.ntotal == 0:
            return []

        q = np.array([query_vector], dtype=np.float32)
        distances, indices = self.index.search(q, min(top_k, self.index.ntotal))

        results: list[tuple[str, float, dict[str, Any]]] = []
        for dist, idx in zip(distances[0], indices[0]):
            if idx == -1:
                continue  # FAISS may return -1 when there aren't enough results
            ext_id = self._reverse_map.get(int(idx), f"unknown_{idx}")
            meta = self._metadata.get(ext_id, {})
            results.append((ext_id, float(dist), meta))

        return results

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, path: str | Path) -> None:
        """Persist the FAISS index and metadata to disk.

        Creates two files:
          * ``<path>.index`` — the FAISS binary index
          * ``<path>.meta``  — the mapping/metadata pickle
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        faiss.write_index(self.index, str(path.with_suffix(".index")))

        meta_payload = {
            "dimension": self.dimension,
            "id_map": self._id_map,
            "reverse_map": {str(k): v for k, v in self._reverse_map.items()},
            "metadata": self._metadata,
            "next_pos": self._next_pos,
        }
        with open(path.with_suffix(".meta"), "wb") as f:
            pickle.dump(meta_payload, f)

    def load(self, path: str | Path) -> None:
        """Load a previously saved index and metadata from disk."""
        path = Path(path)

        if faiss is None:
            raise ImportError("faiss is required to load a vector store")

        index_file = path.with_suffix(".index")
        meta_file = path.with_suffix(".meta")

        if not index_file.exists():
            raise FileNotFoundError(f"FAISS index file not found: {index_file}")
        if not meta_file.exists():
            raise FileNotFoundError(f"Metadata file not found: {meta_file}")

        self.index = faiss.read_index(str(index_file))
        self.dimension = self.index.d

        with open(meta_file, "rb") as f:
            meta_payload = pickle.load(f)

        self._id_map = meta_payload["id_map"]
        self._reverse_map = {int(k): v for k, v in meta_payload["reverse_map"].items()}
        self._metadata = meta_payload["metadata"]
        self._next_pos = meta_payload["next_pos"]

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    @property
    def size(self) -> int:
        """Number of vectors currently in the index."""
        return self.index.ntotal

    def clear(self) -> None:
        """Reset the index, discarding all vectors and metadata."""
        self.index = faiss.IndexFlatL2(self.dimension)
        self._id_map.clear()
        self._reverse_map.clear()
        self._metadata.clear()
        self._next_pos = 0
