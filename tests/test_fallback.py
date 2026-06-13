"""FallbackChain 单元测试。

覆盖场景：
- 第一次就成功（不应触发重试/降级）
- RetryableError 重试后成功
- NonRetryableError 立即跳下个模型（不重试）
- RetryableError 持续到 max_retries 用尽后跳下个模型
- 降级到 primary.fallback_model_ids 中的 secondary
- 所有模型失败抛 AllModelsFailedError（错误消息含所有尝试过的模型名）
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from backend.core.errors import (
    AllModelsFailedError,
    NonRetryableError,
    RetryableError,
)
from backend.services.fallback import FallbackChain


def _make_model(model_id: int, name: str, *, fallback_model_ids=None):
    return SimpleNamespace(
        id=model_id,
        model_name=name,
        fallback_model_ids=list(fallback_model_ids or []),
    )


# ---------- 1. 第一次就成功 ----------

def test_success_first_try():
    """operation 第一次返回成功 → 整个 chain 1 次调用即结束。"""
    primary = _make_model(1, "primary")
    secondary = _make_model(2, "secondary")
    chain = FallbackChain(models=[primary, secondary], max_retries=2)

    calls: list[str] = []

    async def op(model):
        calls.append(model.model_name)
        return "ok"

    result = asyncio.run(
        chain.execute_with_fallback(primary=primary, operation=op, request_type="chat")
    )
    assert result == "ok"
    assert calls == ["primary"]


# ---------- 2. RetryableError 重试后成功 ----------

def test_retry_on_retryable_then_success():
    """第一次抛 RetryableError，第二次成功。max_retries=2 时不应跳下个模型。"""
    primary = _make_model(1, "primary")
    chain = FallbackChain(models=[primary], max_retries=2)

    calls: list[tuple[str, int]] = []
    attempt = 0

    async def op(model):
        nonlocal attempt
        calls.append((model.model_name, attempt))
        if attempt < 1:
            attempt += 1
            raise RetryableError("rate limit")
        return "ok"

    async def fake_sleep(_):
        return None

    result = asyncio.run(
        chain.execute_with_fallback(
            primary=primary, operation=op, request_type="chat", sleep_fn=fake_sleep
        )
    )

    assert result == "ok"
    # 同一模型调了 2 次（attempt 0, 1）
    assert [c[0] for c in calls] == ["primary", "primary"]


# ---------- 3. NonRetryableError 立即跳下个模型 ----------

def test_nonretryable_break_immediately():
    """NonRetryableError 不重试，直接跳下个模型。"""
    primary = _make_model(1, "primary", fallback_model_ids=[2])
    secondary = _make_model(2, "secondary")
    chain = FallbackChain(models=[primary, secondary], max_retries=3)

    calls: list[str] = []

    async def op(model):
        calls.append(model.model_name)
        if model.model_name == "primary":
            raise NonRetryableError("bad request")
        return "ok"

    async def fake_sleep(_):
        return None

    result = asyncio.run(
        chain.execute_with_fallback(
            primary=primary, operation=op, request_type="chat", sleep_fn=fake_sleep
        )
    )

    assert result == "ok"
    # primary 只调了 1 次（不重试）→ 立刻跳 secondary
    assert calls == ["primary", "secondary"]


# ---------- 4. RetryableError 用尽后跳下个模型 ----------

def test_max_retries_exhausted():
    """持续 RetryableError → 重试 max_retries+1 次后跳下个模型。"""
    primary = _make_model(1, "primary", fallback_model_ids=[2])
    secondary = _make_model(2, "secondary")
    chain = FallbackChain(models=[primary, secondary], max_retries=2)

    primary_calls: list[int] = []
    secondary_calls: list[int] = []

    async def op(model):
        if model.model_name == "primary":
            primary_calls.append(len(primary_calls))
            raise RetryableError("timeout")
        # secondary 也持续失败 → 触发 AllModelsFailedError
        secondary_calls.append(len(secondary_calls))
        raise RetryableError("timeout2")

    async def fake_sleep(_):
        return None

    with pytest.raises(AllModelsFailedError) as excinfo:
        asyncio.run(
            chain.execute_with_fallback(
                primary=primary, operation=op, request_type="chat", sleep_fn=fake_sleep
            )
        )

    # max_retries=2 → 调 3 次 (0,1,2) for primary, 3 次 for secondary
    assert len(primary_calls) == 3
    assert len(secondary_calls) == 3
    # 错误消息应含两个模型名
    assert "primary" in str(excinfo.value)
    assert "secondary" in str(excinfo.value)


# ---------- 5. 降级到 secondary ----------

def test_fallback_to_secondary_model():
    """primary 失败后，chain 应按 primary.fallback_model_ids 顺序切到 secondary。"""
    primary = _make_model(1, "primary", fallback_model_ids=[3])  # 注意：fallback 是 3，不是 2
    secondary_model = _make_model(3, "alt-model")
    unrelated = _make_model(2, "unrelated")  # 不在 fallback_ids 里
    chain = FallbackChain(models=[primary, unrelated, secondary_model], max_retries=0)

    calls: list[str] = []

    async def op(model):
        calls.append(model.model_name)
        if model.model_name == "primary":
            raise RetryableError("fail")
        return f"ok-from-{model.model_name}"

    async def fake_sleep(_):
        return None

    result = asyncio.run(
        chain.execute_with_fallback(
            primary=primary, operation=op, request_type="chat", sleep_fn=fake_sleep
        )
    )

    # 应只调 primary 和 alt-model（id=3），跳过 unrelated
    assert calls == ["primary", "alt-model"]
    assert result == "ok-from-alt-model"


# ---------- 6. 全部失败 ----------

def test_all_models_failed():
    """所有模型都失败时，抛 AllModelsFailedError，错误消息含所有尝试过的模型名。"""
    primary = _make_model(1, "primary", fallback_model_ids=[2, 3])
    m2 = _make_model(2, "model-b")
    m3 = _make_model(3, "model-c")
    chain = FallbackChain(models=[primary, m2, m3], max_retries=1)

    async def op(model):
        raise NonRetryableError(f"hard fail in {model.model_name}")

    async def fake_sleep(_):
        return None

    with pytest.raises(AllModelsFailedError) as excinfo:
        asyncio.run(
            chain.execute_with_fallback(
                primary=primary, operation=op, request_type="chat", sleep_fn=fake_sleep
            )
        )

    err = excinfo.value
    # tried 应含全部 3 个模型
    assert "primary" in err.tried
    assert "model-b" in err.tried
    assert "model-c" in err.tried
    # 错误消息也应含这些模型名
    msg = str(err)
    assert "primary" in msg
    assert "model-b" in msg
    assert "model-c" in msg
    # last_error 应保留最后一个错误
    assert isinstance(err.last_error, NonRetryableError)
