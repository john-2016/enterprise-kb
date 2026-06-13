"""A/B 分流 selector。

职责：根据 ABTestRule 配置把 user_id 映射到具体模型。

设计：
- ``ABStrategy`` 枚举：``USER_HASH_MOD``（稳定 hash 桶） / ``RANDOM_WEIGHT``（权重随机）
- ``ABRuleConfig`` dataclass：装载 ORM 行的运行时等价物（方便测试用 SimpleNamespace 注入）
- ``select_model_by_ab`` 是纯函数 — 无 IO、易测

调用方（ModelRouter）负责把 ORM 行转成 ``ABRuleConfig``，selector 只关心策略。
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Protocol, Sequence


class ABStrategy(str, Enum):
    """分流策略枚举。值与 ORM 中 ``ab_test_rules.strategy`` 列保持一致。"""

    USER_HASH_MOD = "user_hash_mod"
    RANDOM_WEIGHT = "random_weight"


@dataclass
class ABRuleConfig:
    """ABTestRule ORM 行的运行时等价物。

    与 ORM 解耦：测试可以直接传 SimpleNamespace / dataclass，selector 不感知。
    """

    strategy: ABStrategy
    config: dict = field(default_factory=dict)
    enabled: bool = True
    target: str = "chat"

    @classmethod
    def from_orm(cls, orm_row: Any) -> "ABRuleConfig":
        """从 ORM 行构造（容忍 strategy 是 str 或 enum）。"""
        strat = orm_row.strategy
        if isinstance(strat, ABStrategy):
            strategy = strat
        else:
            strategy = ABStrategy(strat)
        return cls(
            strategy=strategy,
            config=dict(orm_row.config or {}),
            enabled=bool(getattr(orm_row, "enabled", True)),
            target=str(getattr(orm_row, "target", "chat")),
        )


class ModelLike(Protocol):
    """selector 需要的 model 最小接口（duck-typing）。"""

    id: int
    model_name: str
    is_default_chat: bool
    is_default_emb: bool


def _get_default(target: str, all_models: Sequence[ModelLike]) -> ModelLike | None:
    """按 target 返回默认模型；找不到返回 None（调用方决定是否抛错）。"""
    want_emb = target == "embedding"
    for m in all_models:
        if want_emb and getattr(m, "is_default_emb", False):
            return m
        if not want_emb and getattr(m, "is_default_chat", False):
            return m
    return None


def _find_by_name(name: str, all_models: Sequence[ModelLike]) -> ModelLike | None:
    """按 model_name 找模型。"""
    for m in all_models:
        if getattr(m, "model_name", None) == name:
            return m
    return None


def _apply_user_hash_mod(
    user_id: int, config: Mapping[str, Any], all_models: Sequence[ModelLike]
) -> ModelLike:
    """USER_HASH_MOD 策略实现。

    config 形如 ``{"mod": 3, "mapping": {"0": "m1", "1": "m2", ...}}``。
    桶号 = ``user_id % mod``，再用 mapping[桶号] 拿模型名。
    """
    mod = int(config.get("mod", 1))
    if mod <= 0:
        raise ValueError(f"user_hash_mod.mod must be > 0, got {mod}")
    mapping = dict(config.get("mapping") or {})
    if not mapping:
        raise ValueError("user_hash_mod.config.mapping is empty")

    bucket = str(user_id % mod)
    name = mapping.get(bucket)
    if name is None:
        raise ValueError(
            f"user_hash_mod mapping missing bucket {bucket!r}; "
            f"have buckets {sorted(mapping.keys())}"
        )
    assert name is not None  # for type checker
    model = _find_by_name(name, all_models)
    if model is None:
        raise ValueError(
            f"user_hash_mod mapped to unknown model {name!r}; "
            f"available: {[m.model_name for m in all_models]}"
        )
    return model


def _apply_random_weight(
    user_id: int, config: Mapping[str, Any], all_models: Sequence[ModelLike]
) -> ModelLike:
    """RANDOM_WEIGHT 策略实现。

    config 形如 ``{"weights": {"m1": 0.7, "m2": 0.3}}``。
    按权重做加权随机选择（user_id 暂不使用，但保留入参以对齐签名）。
    """
    weights_raw = dict(config.get("weights") or {})
    if not weights_raw:
        raise ValueError("random_weight.config.weights is empty")

    # 过滤掉 weight<=0 的（不应被选）
    positive = {n: w for n, w in weights_raw.items() if w > 0}
    if not positive:
        raise ValueError("random_weight all weights are zero / negative")
    total = sum(positive.values())
    if total <= 0:
        raise ValueError("random_weight weight sum is zero")

    # 加权抽样
    pick = random.random() * total
    cum = 0.0
    chosen_name: str | None = None
    for name, w in positive.items():
        cum += w
        if pick <= cum:
            chosen_name = name
            break
    if chosen_name is None:
        # 浮点边界兜底：取最后一个
        chosen_name = list(positive.keys())[-1]

    model = _find_by_name(chosen_name, all_models)
    if model is None:
        raise ValueError(
            f"random_weight picked unknown model {chosen_name!r}; "
            f"available: {[m.model_name for m in all_models]}"
        )
    return model


def select_model_by_ab(
    user_id: int,
    target: str,
    rules: Sequence[Any],
    all_models: Sequence[ModelLike],
) -> ModelLike:
    """根据 AB 规则为 user_id 选一个模型。

    流程：
    1. 过滤出 target 匹配 & enabled=True 的规则
    2. 用第一条匹配的规则计算模型
    3. 无规则 / 都不匹配 → 走默认

    :param user_id: 用户 ID（int）
    :param target: ``"chat"`` 或 ``"embedding"``
    :param rules: 任意带 ``strategy``/``config``/``enabled``/``target`` 属性的对象
    :param all_models: 全部可用模型
    :returns: 选中的模型
    :raises ValueError: 找不到可用模型
    """
    for rule in rules or []:
        # 跳过 disabled 或 target 不匹配的
        if not getattr(rule, "enabled", True):
            continue
        if getattr(rule, "target", "chat") != target:
            continue

        # 把 strategy 标准化成 ABStrategy
        raw_strategy = getattr(rule, "strategy", None)
        try:
            strategy = raw_strategy if isinstance(raw_strategy, ABStrategy) else ABStrategy(str(raw_strategy))
        except ValueError as e:
            raise ValueError(f"unknown AB strategy: {raw_strategy!r}") from e

        cfg = dict(getattr(rule, "config", None) or {})

        if strategy is ABStrategy.USER_HASH_MOD:
            return _apply_user_hash_mod(user_id, cfg, all_models)
        if strategy is ABStrategy.RANDOM_WEIGHT:
            return _apply_random_weight(user_id, cfg, all_models)

        # 未知 strategy：跳过（不抛错，让其它规则或默认接手）
        continue

    # 无可用规则 → 默认
    default = _get_default(target, all_models)
    if default is None:
        raise ValueError(
            f"no AB rule matched and no default model for target={target!r}"
        )
    return default
