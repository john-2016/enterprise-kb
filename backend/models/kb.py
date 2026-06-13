"""KnowledgeBase model + many-to-many association table with Document."""

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base


class KnowledgeBase(Base):
    """A logical knowledge base that groups related documents together."""

    __tablename__ = "knowledge_bases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # --- relationships ---
    owner: Mapped["User"] = relationship("User")  # noqa: F821
    document_associations: Mapped[list["DocumentKB"]] = relationship(  # noqa: F821
        "DocumentKB", back_populates="knowledge_base", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<KnowledgeBase id={self.id} name={self.name!r}>"


class DocumentKB(Base):
    """Many-to-many association between KnowledgeBase and Document."""

    __tablename__ = "document_kb"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    kb_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    document_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # --- relationships ---
    knowledge_base: Mapped["KnowledgeBase"] = relationship(
        "KnowledgeBase", back_populates="document_associations"
    )
    document: Mapped["Document"] = relationship(
        "Document", back_populates="kb_associations"
    )

    def __repr__(self) -> str:
        return (
            f"<DocumentKB id={self.id} "
            f"kb_id={self.kb_id} document_id={self.document_id}>"
        )
