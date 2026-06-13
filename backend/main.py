"""Enterprise Knowledge Base — FastAPI Application Entry Point."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.config import settings
from backend.database import Base, get_engine, init_db
from backend.models import User, Document, KnowledgeBase, DocumentKB, AuditLog
from backend.routers import auth, documents, chat, admin
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

    # 1. Database
    await init_db(settings.DATABASE_URL)
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

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

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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

# Frontend SPA — catch-all mount AFTER API routes
static_dir = Path(__file__).resolve().parent.parent / "frontend"
if static_dir.exists():
    app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="frontend")
