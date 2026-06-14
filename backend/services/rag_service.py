"""RAG (Retrieval-Augmented Generation) service.

Orchestrates: question embedding → vector-store retrieval → context assembly
→ MiniMax M3 chat completion → audit logging.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from .embedding_service import (
    DEFAULT_CHAT_MODEL,
    MINIMAX_CHAT_URL,
    MiniMaxEmbedding,
    VectorStore,
)

# ---------------------------------------------------------------------------
# Default prompt template
# ---------------------------------------------------------------------------

_DEFAULT_SYSTEM_PROMPT = (
    "你是一个企业知识库助手。请**仅基于**下面提供的上下文内容回答用户问题。"
    "如果上下文信息不足，请明确说明——不要编造信息。"
    "尽可能引用相关文档来源。\n\n"
    "上下文：\n{context}"
)

_DEFAULT_RAG_PROMPT = (
    "基于以上上下文，回答以下问题：\n\n{question}"
)

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class RAGError(Exception):
    """Raised when the RAG pipeline fails."""


# ===================================================================
# RAGService
# ===================================================================

class RAGService:
    """Retrieval-Augmented Generation pipeline.

    Usage::

        embedder = MiniMaxEmbedding()
        store = VectorStore(dimension=1536)
        rag = RAGService(embedder, store)

        answer = await rag.query("What is the vacation policy?")
    """

    def __init__(
        self,
        embedding_service: MiniMaxEmbedding,
        vector_store: VectorStore,
        system_prompt: str | None = None,
        rag_prompt: str | None = None,
    ) -> None:
        self.embedder = embedding_service
        self.store = vector_store
        self.system_prompt = system_prompt or _DEFAULT_SYSTEM_PROMPT
        self.rag_prompt = rag_prompt or _DEFAULT_RAG_PROMPT
        self._client: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------
    # HTTP client lifecycle
    # ------------------------------------------------------------------

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=120.0)
        return self._client

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # Core query method
    # ------------------------------------------------------------------

    async def query(
        self,
        question: str,
        top_k: int = 5,
    ) -> str:
        """Run the full RAG pipeline and return the generated answer.

        Steps
        -----
        1. Embed the question via ``MiniMaxEmbedding.embed_text``.
        2. Search the vector store for the *top_k* most relevant chunks.
        3. Build a prompt with the retrieved context.
        4. Call the MiniMax M3 chat API for an answer.
        5. Log the query to the audit trail.
        6. Return the answer.
        """
        if not question.strip():
            raise RAGError("Question cannot be empty")

        # --- Step 1: Embed ---
        query_vector = await self.embedder.embed_text(question)

        # --- Step 2: Retrieve ---
        results = self.store.search(query_vector, top_k=top_k)

        # --- Step 3: Build context ---
        context_parts: list[str] = []
        for idx, (doc_id, score, meta) in enumerate(results, 1):
            chunk_text = meta.get("text", meta.get("content", ""))
            source = meta.get("filename", meta.get("source", doc_id))
            context_parts.append(
                f"[{idx}] (source: {source}, relevance: {score:.4f})\n{chunk_text}"
            )

        context = "\n\n---\n\n".join(context_parts) if context_parts else (
            "No relevant documents were found in the knowledge base."
        )

        # --- Step 4: Call MiniMax M3 ---
        answer = await self._call_chat_api(question, context)

        # --- Step 5: Audit log ---
        await self._log_audit(
            question=question,
            top_k=top_k,
            retrieved_ids=[r[0] for r in results],
            answer=answer,
        )

        return answer

    # ------------------------------------------------------------------
    # MiniMax M3 chat call
    # ------------------------------------------------------------------

    async def _call_chat_api(
        self,
        question: str,
        context: str,
    ) -> str:
        """Call the MiniMax ``M3`` chat completion API.

        Reads ``MINIMAX_API_KEY`` from the environment.
        """
        api_key = os.environ.get("MINIMAX_API_KEY")
        if not api_key:
            raise RAGError(
                "MINIMAX_API_KEY environment variable is not set. "
                "Cannot call MiniMax chat API."
            )

        client = await self._get_client()

        system_text = self.system_prompt.format(context=context)
        user_text = self.rag_prompt.format(question=question)

        payload = {
            "model": DEFAULT_CHAT_MODEL,
            "messages": [
                {"role": "system", "content": system_text},
                {"role": "user", "content": user_text},
            ],
            "temperature": 0.3,
            "max_tokens": 2048,
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        try:
            resp = await client.post(
                MINIMAX_CHAT_URL,
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()

            # MiniMax M3 OpenAI-compatible response:
            # data["choices"][0]["message"]["content"]
            choices = data.get("choices", [])
            if not choices:
                raise RAGError("MiniMax API 返回内容为空")

            content = choices[0].get("message", {}).get("content", "")
            if not content:
                raise RAGError("MiniMax API 返回内容为空")
            return content

        except httpx.HTTPStatusError as exc:
            raise RAGError(
                f"MiniMax chat API error {exc.response.status_code}: "
                f"{exc.response.text}"
            ) from exc
        except httpx.RequestError as exc:
            raise RAGError(f"MiniMax chat API request failed: {exc}") from exc

    # ------------------------------------------------------------------
    # Audit logging
    # ------------------------------------------------------------------

    async def _log_audit(
        self,
        question: str,
        top_k: int,
        retrieved_ids: list[str],
        answer: str,
    ) -> None:
        """Write a structured audit log entry.

        In production this would write to a database table or a centralised
        logging system. Here we append to a local JSON-lines file as a
        reasonable default.
        """
        log_dir = os.environ.get("RAG_AUDIT_DIR", "/var/log/enterprise-kb")
        os.makedirs(log_dir, exist_ok=True)
        log_file = os.path.join(log_dir, "rag_audit.jsonl")

        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "question": question,
            "top_k": top_k,
            "retrieved_document_ids": retrieved_ids,
            "answer_preview": answer[:500],  # truncate to keep logs lean
            "answer_length": len(answer),
        }

        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
