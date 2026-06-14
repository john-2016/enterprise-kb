"""跨模块共享的错误类型。

设计：把错误类放在 core 层（而不是 service 层），
让 service/model_clients 都能 import 而不互相依赖。
"""
from __future__ import annotations

from typing import Sequence


class ModelClientError(Exception):
    """模型客户端基类错误。"""


class RetryableError(ModelClientError):
    """可重试错误（限流、5xx 等）。

    FallbackChain 会重试 N 次后跳到下个模型。
    """


class NonRetryableError(ModelClientError):
    """不可重试错误（鉴权失败、参数错、上下文超长等）。

    FallbackChain 立即跳到下个模型，不重试当前。
    """


class AllModelsFailedError(ModelClientError):
    """所有模型（主 + 备选）都失败了。"""

    def __init__(self, tried: Sequence[str], last_error: Exception | None = None):
        self.tried = list(tried)
        self.last_error = last_error
        msg = f"All {len(self.tried)} models failed: {self.tried}"
        if last_error is not None:
            msg += f". Last error: {type(last_error).__name__}: {last_error}"
        super().__init__(msg)
