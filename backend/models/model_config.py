"""ModelConfig ORM — 单个具体模型的配置。

设计要点：
- ``provider_id`` FK → model_providers.id，级联删除
- ``UniqueConstraint(provider_id, model_name)``：同 provider 下模型名唯一
- ``is_default_chat`` / ``is_default_emb``：业务上要求"全表只能各有一个 True"
  — 用部分唯一索引（``Index(..., sqlite_where=...)``）只约束 True 的行
- ``model_type`` 限定为 ``chat`` / ``embedding``（业务枚举）
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base


class ModelConfig(Base):
    __tablename__ = "model_configs"
    __table_args__ = (
        UniqueConstraint("provider_id", "model_name", name="uq_provider_model_name"),
        # 部分唯一索引：只约束 is_default_* = True 的行（False 多行不冲突）
        # 注意：SQLAlchemy 2.0 中 Index(..., sqlite_where=...) 的 where 子句必须用 SQL 表达式
        # 用 text() 避免自引用问题
        Index(
            "uq_default_chat",
            "is_default_chat",
            unique=True,
            sqlite_where=text("is_default_chat = 1"),
            postgresql_where=text("is_default_chat = true"),
        ),
        Index(
            "uq_default_emb",
            "is_default_emb",
            unique=True,
            sqlite_where=text("is_default_emb = 1"),
            postgresql_where=text("is_default_emb = true"),
        ),
        Index("ix_model_configs_provider_id", "provider_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider_id: Mapped[int] = mapped_column(
        ForeignKey("model_providers.id", ondelete="CASCADE"), nullable=False
    )
    model_name: Mapped[str] = mapped_column(String(128), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    model_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    context_window: Mapped[int] = mapped_column(Integer, default=128000, nullable=False)
    input_price: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    output_price: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_default_chat: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_default_emb: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    extra_config: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # 反向：provider → model
    provider: Mapped["ModelProvider"] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "ModelProvider", back_populates="models", lazy="select"
    )

    def __repr__(self) -> str:
        return f"<ModelConfig id={self.id} {self.model_name!r} type={self.model_type!r}>"
