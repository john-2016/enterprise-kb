"""Enterprise Knowledge Base — FastAPI Application Entry Point."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.config import settings, validate_security_settings
from backend.database import Base, get_engine, init_db
from backend import database as _database  # Phase 7 fix: use module reference (init_db rebinds module global; top-level import of AsyncSessionLocal captured None at import time)
from backend.models import User, Document, KnowledgeBase, DocumentKB, AuditLog
from backend.routers import auth, documents, chat, admin
from backend.routers import admin_providers, admin_models, admin_ab_rules
from backend.routers import admin_metrics
from backend.services.embedding_service import MiniMaxEmbedding, VectorStore

DATA_DIR = Path(settings.DATA_DIR)
VECTOR_DIR = DATA_DIR / "vector_store"
UPLOAD_DIR = DATA_DIR / "uploads"
VECTOR_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# Global state
embedding_service: MiniMaxEmbedding | None = None
vector_store: VectorStore | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle — init DB, embedding, vector store."""
    global embedding_service, vector_store

    # 0. Security validation (C1)
    validate_security_settings()

    # 1. Database
    await init_db(settings.DATABASE_URL)
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # 1.5 v1 → v2 auto migration (Phase 5 — seed built-in minimax provider if missing)
    try:
        from backend.scripts.migrate_v1_to_v2 import migrate  # type: ignore  # noqa: E402
    except Exception:  # pragma: no cover
        # Package-relative import path (used when imported as `scripts.migrate_v1_to_v2`)
        from scripts.migrate_v1_to_v2 import migrate  # type: ignore  # noqa: E402

    try:
        async with _database.AsyncSessionLocal() as _s:
            await migrate(_s)
    except Exception as _exc:  # pragma: no cover — boot must not break
        import logging as _logging
        _logging.getLogger("kb.startup").warning("v1→v2 migration skipped: %s", _exc)

    # 2. Embedding service (MiniMax embo-01)
    embedding_service = MiniMaxEmbedding()

    # 3. Vector store
    vs_base = VECTOR_DIR / "vs"
    vector_store = VectorStore(dimension=settings.VECTOR_DIMENSION)
    if (VECTOR_DIR / "vs.index").exists():
        vector_store.load(str(vs_base))

    yield

    # Shutdown — persist vector store
    if vector_store is not None:
        vector_store.save(str(vs_base))


app = FastAPI(
    title="Enterprise Knowledge Base",
    description="企业级知识库系统 — MiniMax RAG 引擎",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS — 白名单（来自 CORS_ALLOWED_ORIGINS），禁用 *+credentials
_origins = [o.strip() for o in settings.CORS_ALLOWED_ORIGINS.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=settings.CORS_ALLOW_CREDENTIALS,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept", "Origin", "X-Requested-With"],
)


# Health check (BEFORE static mount so API routes take precedence)
@app.get("/api/v1/health")
async def health():
    return {"status": "ok", "version": "1.0.0", "service": "enterprise-kb"}


# API Routes
app.include_router(auth.router)
app.include_router(documents.router)
app.include_router(chat.router)
app.include_router(admin.router)
app.include_router(admin_providers.router)
if admin_models.router is not None:
    app.include_router(admin_models.router)
if admin_ab_rules.router is not None:
    app.include_router(admin_ab_rules.router)
app.include_router(admin_metrics.router)

# Frontend SPA — catch-all mount AFTER API routes
static_dir = Path(__file__).resolve().parent.parent / "frontend"
if static_dir.exists():
    app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="frontend")
