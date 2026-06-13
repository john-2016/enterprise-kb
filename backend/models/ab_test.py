"""A/B 测试相关 ORM：ABTestRule + ABTestMetric。

设计要点：
- ABTestRule：定义一个分流规则（策略 + 配置 + 目标）
- ABTestMetric：每次 chat/embed 调用的实际表现（延迟、token、用户反馈）
- feedback 字段：-1=👎, 0=中性, 1=👍, NULL=未反馈
- model_id 关联到 ModelConfig；user_id 关联到 User
- 级联删除：删 user/model 时自动清理 metrics
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    SmallInteger,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class ABTestRule(Base):
    __tablename__ = "ab_test_rules"
    __table_args__ = (
        Index("ix_ab_test_rules_target_enabled", "target", "enabled"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    enabled: Mapped[bool] = mapped_column(default=True, nullable=False)
    strategy: Mapped[str] = mapped_column(String(32), nullable=False)  # user_hash_mod / random_weight
    target: Mapped[str] = mapped_column(String(16), nullable=False)  # chat / embedding
    config: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    # user_hash_mod 形如 {"mod": 3, "mapping": {"0": "m1", "1": "m2", "2": "m3"}}
    # random_weight 形如 {"weights": {"m1": 0.7, "m2": 0.3}}
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<ABTestRule id={self.id} {self.name!r} {self.strategy!r} target={self.target!r}>"


class ABTestMetric(Base):
    __tablename__ = "ab_test_metrics"
    __table_args__ = (
        Index("ix_metrics_model_created", "model_id", "created_at"),
        Index("ix_metrics_user_created", "user_id", "created_at"),
        Index("ix_metrics_ab_rule", "ab_rule_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    model_id: Mapped[int] = mapped_column(
        ForeignKey("model_configs.id", ondelete="CASCADE"), nullable=False
    )
    ab_rule_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ab_test_rules.id", ondelete="SET NULL"), nullable=True
    )
    request_type: Mapped[str] = mapped_column(String(16), nullable=False)  # chat / embedding
    latency_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    feedback: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)  # -1 / 0 / 1
    feedback_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    def __repr__(self) -> str:
        return f"<ABTestMetric id={self.id} model={self.model_id} fb={self.feedback}>"
