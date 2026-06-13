"""SQLAlchemy ORM models for the enterprise knowledge base.

Export all models so they can be imported from ``backend.models``,
ensuring SQLAlchemy's metaclass registers every table on ``Base.metadata``.
Also re-export ``Base`` so Alembic's ``env.py`` can import it for autogenerate.
"""
from backend.database import Base
from backend.models.user import User
from backend.models.document import Document
from backend.models.kb import KnowledgeBase, DocumentKB
from backend.models.audit import AuditLog
from backend.models.provider import ModelProvider
from backend.models.model_config import ModelConfig

__all__ = [
    "Base",
    "User",
    "Document",
    "KnowledgeBase",
    "DocumentKB",
    "AuditLog",
    "ModelProvider",
    "ModelConfig",
]
