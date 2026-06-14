"""FallbackChain — 多模型降级 + 单模型内重试。

职责：把"主模型失败 → 切到下一个"这件事封装成一个可重用的协程壳。

设计：
- chain 顺序：``[primary] + [m for m in models if m.id in primary.fallback_model_ids]``
  （保留 primary.fallback_model_ids 的相对顺序）
- 每个模型：``for attempt in range(max_retries + 1)`` 次重试，重试间 ``asyncio.sleep(2**attempt)``
  做指数退避（生产可以注入 sleep 加速测试）
- 错误分类：
  - ``NonRetryableError`` → 立即跳出当前模型的内层重试循环，进入下个模型
  - ``RetryableError`` → 继续重试；用尽 ``max_retries+1`` 次后跳下个模型
  - 其他 ``Exception`` → 当作不可重试（保守策略：避免隐藏 bug 反复重试）
- 全部失败：抛 ``AllModelsFailedError(tried, last_error)``
"""
from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, Sequence

from backend.core.errors import (
    AllModelsFailedError,
    NonRetryableError,
    RetryableError,
)


# 协程型 operation 签名：接受一个 model-like 对象，返回结果
Operation = Callable[[object], Awaitable[object]]


class FallbackChain:
    """多模型降级链 + 单模型内重试。"""

    def __init__(
        self,
        models: Sequence[object],
        max_retries: int = 2,
        sleep_fn: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        """
        :param models: 全表可用模型（用于查找 fallback）
        :param max_retries: 单个模型内部最大重试次数（不含首次，所以共 ``max_retries+1`` 次尝试）
        :param sleep_fn: 可注入的 sleep（测试中可换成 fake_sleep）
        """
        self.models = list(models)
        if max_retries < 0:
            raise ValueError(f"max_retries must be >= 0, got {max_retries}")
        self.max_retries = max_retries
        # 默认走 asyncio.sleep；测试可显式传 fake_sleep
        self.sleep_fn: Callable[[float], Awaitable[None]] = (
            sleep_fn if sleep_fn is not None else asyncio.sleep
        )

    # ------------------------------------------------------------------ #
    # 公开 API                                                          #
    # ------------------------------------------------------------------ #

    async def execute_with_fallback(
        self,
        primary: object,
        operation: Operation,
        request_type: str = "chat",
        sleep_fn: Callable[[float], Awaitable[None]] | None = None,
    ) -> object:
        """执行 operation（chat / embed），失败时按链降级。

        :param primary: 首选模型（带 ``id`` / ``model_name`` / ``fallback_model_ids``）
        :param operation: 协程函数 ``async def op(model) -> Any``
        :param request_type: ``"chat"`` / ``"embedding"``（仅日志/扩展用）
        :param sleep_fn: 可选：覆盖默认 sleep（测试中可换 fake_sleep 加速）
        :returns: 第一个成功的 operation 返回值
        :raises AllModelsFailedError: 所有模型（含降级）都失败
        """
        chain = self._build_chain(primary)
        tried: list[str] = []
        last_error: Exception | None = None
        # 优先用本次调用传入的 sleep_fn（测试用）
        effective_sleep = sleep_fn if sleep_fn is not None else self.sleep_fn

        for model in chain:
            model_name = getattr(model, "model_name", getattr(model, "id", "?"))
            tried.append(str(model_name))
            try:
                return await self._run_one(model, operation, effective_sleep)
            except NonRetryableError as e:
                # 不可重试 → 立刻换下个模型
                last_error = e
                continue
            except RetryableError as e:
                # 已经在外层 try/except 中处理掉"用尽"的情况
                last_error = e
                continue

        # 所有模型都失败
        raise AllModelsFailedError(tried=tried, last_error=last_error)

    # ------------------------------------------------------------------ #
    # 内部                                                              #
    # ------------------------------------------------------------------ #

    def _build_chain(self, primary: object) -> list[object]:
        """构造本次调用的实际 chain：``[primary] + [fallback...]``。"""
        fallback_ids = list(getattr(primary, "fallback_model_ids", []) or [])
        # 按 primary.fallback_model_ids 的顺序在 models 里查找（保留用户指定顺序）
        by_id = {getattr(m, "id", None): m for m in self.models}
        fallbacks = [by_id[fid] for fid in fallback_ids if fid in by_id]
        return [primary] + fallbacks

    async def _run_one(
        self,
        model: object,
        operation: Operation,
        sleep_fn: Callable[[float], Awaitable[None]],
    ) -> object:
        """在单个模型上跑 operation，处理重试。"""
        total_attempts = self.max_retries + 1
        last_exc: Exception | None = None
        for attempt in range(total_attempts):
            try:
                return await operation(model)
            except NonRetryableError:
                # 不可重试 → 直接抛给外层（外层会 continue 到下个模型）
                raise
            except RetryableError as e:
                last_exc = e
                if attempt < total_attempts - 1:
                    # 还有重试机会 → 指数退避后重试
                    await sleep_fn(float(2**attempt))
                    continue
                # 重试用尽 → 抛给外层
                raise
            except Exception as e:
                # 未知异常：保守当作不可重试（避免隐藏 bug 反复打）
                raise NonRetryableError(
                    f"unexpected error from {getattr(model, 'model_name', '?')}: {e}"
                ) from e
        # 理论上不会到这里（循环要么 return 要么 raise）
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("unreachable: _run_one exited without return/raise")
