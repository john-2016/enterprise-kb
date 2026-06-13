"""SQLAlchemy ORM models for the enterprise knowledge base.

Export all models so they can be imported from ``backend.models``,
ensuring SQLAlchemy's metaclass registers every table on ``Base.metadata``.
"""

from backend.models.user import User
from backend.models.document import Document
from backend.models.kb import KnowledgeBase, DocumentKB
from backend.models.audit import AuditLog

__all__ = [
    "User",
    "Document",
    "KnowledgeBase",
    "DocumentKB",
    "AuditLog",
]
