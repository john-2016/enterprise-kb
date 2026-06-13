"""ModelProvider ORM — 第三方模型供应商的注册表。

设计要点：
- ``name`` 唯一（业务标识）
- ``provider_type`` 决定客户端实现（openai_compat / anthropic / gemini / minimax / ...）
- ``api_key_enc`` 存 Fernet 加密后的 bytes，**绝不返回明文**
- ``extra_config`` JSON 字段，存放 provider 特有配置（如 Ollama 的 keep_alive）
- ``is_builtin`` 标记系统内置 provider（不可删，迁移时识别）
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base


class ModelProvider(Base):
    __tablename__ = "model_providers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    provider_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    api_base_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    api_key_enc: Mapped[bytes] = mapped_column(nullable=False)
    extra_config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<ModelProvider id={self.id} name={self.name!r} type={self.provider_type!r}>"
