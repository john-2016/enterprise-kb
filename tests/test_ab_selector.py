"""A/B 分流 selector 单元测试。

覆盖：
- 无规则时回落到默认模型
- USER_HASH_MOD 策略：user_id % mod 决定模型
- 同 user_id 两次调用结果稳定
- RANDOM_WEIGHT 策略：weight=0 的模型永远不被选中
- enabled=False 的规则被忽略
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.services.ab_selector import (
    ABRuleConfig,
    ABStrategy,
    select_model_by_ab,
)


def _make_model(
    model_id: int,
    name: str,
    *,
    is_default_chat: bool = False,
    is_default_emb: bool = False,
    model_type: str | None = None,
):
    """构造一个 model-like 对象，字段名对齐 ModelConfig ORM。"""
    if model_type is None:
        # 兼容旧调用：is_default_chat 暗示 model_type=chat
        if is_default_emb:
            model_type = "embedding"
        elif is_default_chat:
            model_type = "chat"
    return SimpleNamespace(
        id=model_id,
        model_name=name,
        is_default_chat=is_default_chat,
        is_default_emb=is_default_emb,
        model_type=model_type,
    )


def _make_rule(strategy: str, config: dict, *, enabled: bool = True, target: str = "chat"):
    """构造一个 rule-like 对象（兼容 ORM 和 dataclass）。"""
    return SimpleNamespace(
        strategy=strategy,
        config=config,
        enabled=enabled,
        target=target,
    )


# ---------- 1. 无规则时回落到默认模型 ----------

def test_no_rules_returns_default():
    """没有任何规则时，应该返回 is_default_chat=True 的模型。"""
    models = [
        _make_model(1, "gpt-4o-mini", is_default_chat=True),
        _make_model(2, "claude-haiku"),
    ]
    selected = select_model_by_ab(
        user_id=42,
        target="chat",
        rules=[],
        all_models=models,
    )
    assert selected.id == 1
    assert selected.model_name == "gpt-4o-mini"


def test_no_rules_raises_when_no_default():
    """既无规则又无默认模型时，抛 ValueError。"""
    models = [_make_model(1, "gpt-4o-mini"), _make_model(2, "claude-haiku")]
    with pytest.raises(ValueError):
        select_model_by_ab(user_id=0, target="chat", rules=[], all_models=models)


# ---------- 2. USER_HASH_MOD 桶映射 ----------

def test_user_hash_mod_bucket_0():
    """user_id % mod == 0 时映射到 mapping['0'] 对应的模型。"""
    models = [
        _make_model(1, "model-a"),
        _make_model(2, "model-b"),
        _make_model(3, "model-c"),
    ]
    rule = _make_rule(
        "user_hash_mod",
        {"mod": 3, "mapping": {"0": "model-a", "1": "model-b", "2": "model-c"}},
    )
    # 3 % 3 == 0 → model-a
    selected = select_model_by_ab(user_id=3, target="chat", rules=[rule], all_models=models)
    assert selected.model_name == "model-a"

    # 4 % 3 == 1 → model-b
    selected = select_model_by_ab(user_id=4, target="chat", rules=[rule], all_models=models)
    assert selected.model_name == "model-b"

    # 5 % 3 == 2 → model-c
    selected = select_model_by_ab(user_id=5, target="chat", rules=[rule], all_models=models)
    assert selected.model_name == "model-c"

    # 6 % 3 == 0 → model-a（验证 3 % 3 == 0 同样落到第一个）
    selected = select_model_by_ab(user_id=6, target="chat", rules=[rule], all_models=models)
    assert selected.model_name == "model-a"


# ---------- 3. 同一 user_id 多次调用结果稳定 ----------

def test_user_hash_mod_stable():
    """同 user_id 两次调用必须返回同一个模型（分流稳定）。"""
    models = [
        _make_model(1, "model-a"),
        _make_model(2, "model-b"),
    ]
    rule = _make_rule(
        "user_hash_mod",
        {"mod": 2, "mapping": {"0": "model-a", "1": "model-b"}},
    )
    first = select_model_by_ab(user_id=123, target="chat", rules=[rule], all_models=models)
    second = select_model_by_ab(user_id=123, target="chat", rules=[rule], all_models=models)
    assert first.id == second.id


# ---------- 4. RANDOM_WEIGHT：weight=0 永不被选 ----------

def test_random_weight_distribution():
    """weight=0 的模型在 100 次随机中永远不应被选中。"""
    models = [
        _make_model(1, "zero-weight"),
        _make_model(2, "full-weight"),
    ]
    rule = _make_rule(
        "random_weight",
        {"weights": {"zero-weight": 0.0, "full-weight": 1.0}},
    )
    seen_names: set[str] = set()
    for _ in range(100):
        selected = select_model_by_ab(
            user_id=0, target="chat", rules=[rule], all_models=models
        )
        seen_names.add(selected.model_name)
    # zero-weight 不应出现在被选集合里
    assert "zero-weight" not in seen_names
    assert "full-weight" in seen_names


# ---------- 5. enabled=False 的规则被忽略 ----------

def test_disabled_rule_ignored():
    """enabled=False 的规则应被忽略，回落到默认。"""
    models = [
        _make_model(1, "default-chat", is_default_chat=True),
        _make_model(2, "alt-chat"),
    ]
    rule = _make_rule(
        "user_hash_mod",
        {"mod": 2, "mapping": {"0": "alt-chat", "1": "alt-chat"}},
        enabled=False,  # 关键：禁用
    )
    selected = select_model_by_ab(user_id=0, target="chat", rules=[rule], all_models=models)
    # 规则被忽略 → 回落到默认
    assert selected.model_name == "default-chat"


# ---------- 6. target 不匹配的规则被忽略 ----------

def test_target_mismatch_rule_ignored():
    """target='embedding' 的规则不应影响 target='chat' 的选择。"""
    models = [
        _make_model(1, "default-chat", is_default_chat=True),
        _make_model(2, "alt-chat"),
    ]
    rule = _make_rule(
        "user_hash_mod",
        {"mod": 2, "mapping": {"0": "alt-chat", "1": "alt-chat"}},
        target="embedding",  # 关键：目标不匹配
    )
    selected = select_model_by_ab(user_id=0, target="chat", rules=[rule], all_models=models)
    assert selected.model_name == "default-chat"


# ---------- 7. Phase 7 fix: mapping 引用错误能力的模型 → fall through 到默认 ----------

def test_mapping_to_wrong_capability_falls_through():
    """Bug fix: chat 规则的 mapping 引用了 embedding 模型，应 fall through 到默认 chat 模型。"""
    models = [
        _make_model(1, "default-chat", model_type="chat", is_default_chat=True),
        _make_model(2, "alt-chat", model_type="chat"),
        _make_model(3, "emb-model", model_type="embedding", is_default_emb=True),
    ]
    rule = _make_rule(
        "user_hash_mod",
        {"mod": 2, "mapping": {"0": "emb-model", "1": "emb-model"}},
        target="chat",
    )
    # user_id=0 → bucket 0 → "emb-model" 是 embedding 模型 → selector 应跳过规则 → 回落到 default-chat
    selected = select_model_by_ab(user_id=0, target="chat", rules=[rule], all_models=models)
    assert selected.model_name == "default-chat"


def test_mapping_with_mixed_capability_only_picks_chat():
    """Bug fix: mapping 同时含 chat 和 embedding 模型，chat target 只选 chat 模型。"""
    models = [
        _make_model(1, "default-chat", model_type="chat", is_default_chat=True),
        _make_model(2, "alt-chat", model_type="chat"),
        _make_model(3, "emb-model", model_type="embedding", is_default_emb=True),
    ]
    rule = _make_rule(
        "user_hash_mod",
        {"mod": 2, "mapping": {"0": "alt-chat", "1": "emb-model"}},
        target="chat",
    )
    # user_id=0 → bucket 0 → "alt-chat" 是 chat 模型 ✓
    selected0 = select_model_by_ab(user_id=0, target="chat", rules=[rule], all_models=models)
    assert selected0.model_name == "alt-chat"
    # user_id=1 → bucket 1 → "emb-model" 是 embedding → skip rule → default
    selected1 = select_model_by_ab(user_id=1, target="chat", rules=[rule], all_models=models)
    assert selected1.model_name == "default-chat"
