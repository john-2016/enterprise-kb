"""ModelRouter — chat / embed 的统一入口。

职责：
- 根据 AB 规则为 user_id 选模型
- 用 FallbackChain 包裹 provider client 调用
- 注入的 ``get_client_fn`` 让测试能替换真实 provider 工厂

设计：
- 接受 ``ab_rules``（任意带 target/strategy/config/enabled 的对象）+ ``all_models``
- 接受 ``get_client_fn(provider, decrypted_key) → UnifiedModelClient``
- ``default_keys``：``{provider_id: decrypted_key}``（生产从 DB 加载并解密，测试直接传）
- 不接 DB：Phase 3 不做 metrics 记录，留给 Phase 4 接入 chat endpoint 时
"""
from __future__ import annotations

from typing import Any, Callable, Sequence

from backend.services.ab_selector import ABRuleConfig, select_model_by_ab
from backend.services.fallback import FallbackChain
from backend.services.model_clients.base import (
    ChatMessage,
    ChatResponse,
    EmbedResponse,
    UnifiedModelClient,
)


# get_client_fn 签名：(provider, decrypted_key) → UnifiedModelClient
GetClientFn = Callable[[Any, str], UnifiedModelClient]


class ModelRouter:
    """统一的 chat / embed 入口。"""

    def __init__(
        self,
        ab_rules: Sequence[Any],
        all_models: Sequence[Any],
        get_client_fn: GetClientFn,
        default_keys: dict[int, str] | None = None,
        max_retries: int = 2,
        sleep_fn: Callable[[float], Any] | None = None,
    ) -> None:
        """
        :param ab_rules: AB 规则列表（带 target/strategy/config/enabled）
        :param all_models: 全表可用模型
        :param get_client_fn: provider → client 的工厂（注入便于测试）
        :param default_keys: provider_id → 已解密 key 的映射
        :param max_retries: 单模型内重试次数（不含首次）
        :param sleep_fn: 可选 sleep 覆盖（测试用）
        """
        self.ab_rules = list(ab_rules or [])
        self.all_models = list(all_models or [])
        self.get_client_fn = get_client_fn
        self.default_keys = dict(default_keys or {})
        self.max_retries = max_retries
        self.sleep_fn = sleep_fn

        # provider_id → provider object 的索引（加速查找）
        self._providers_by_id: dict[int, Any] = {}
        for m in self.all_models:
            pid = getattr(m, "provider_id", None)
            # model-like 不一定带 provider 对象本身，可能只带 provider_id
            # 这里只缓存 provider_id → placeholder；
            # 实际 provider 对象通过外部注入（生产中 router 不直接管 provider 表）
            # 所以暂存 id 占位，_resolve_provider 时回退到 client_fn 的入参
            if pid is not None:
                self._providers_by_id.setdefault(pid, None)

    # ------------------------------------------------------------------ #
    # 公开 API                                                          #
    # ------------------------------------------------------------------ #

    async def chat(
        self,
        user_id: int,
        target: str,
        messages: Sequence[ChatMessage],
        temperature: float = 0.7,
        max_tokens: int = 1024,
        stream: bool = False,
    ) -> Any:  # 实际是 ChatResponse，但 FallbackChain 返回 Any
        """聊天入口。失败自动降级到 fallback。"""
        primary = self._resolve(user_id, target)
        chain = FallbackChain(
            models=self.all_models,
            max_retries=self.max_retries,
            sleep_fn=self.sleep_fn,
        )
        op = self._build_chat_op(messages, temperature, max_tokens, stream)
        return await chain.execute_with_fallback(
            primary=primary, operation=op, request_type="chat"
        )

    async def embed(
        self,
        user_id: int,
        texts: Sequence[str],
        target: str = "embedding",
    ) -> Any:  # 实际是 EmbedResponse
        """嵌入入口。失败自动降级。"""
        primary = self._resolve(user_id, target)
        chain = FallbackChain(
            models=self.all_models,
            max_retries=self.max_retries,
            sleep_fn=self.sleep_fn,
        )
        op = self._build_embed_op(texts)
        return await chain.execute_with_fallback(
            primary=primary, operation=op, request_type="embedding"
        )

    # ------------------------------------------------------------------ #
    # 解析模型                                                          #
    # ------------------------------------------------------------------ #

    def _resolve(self, user_id: int, target: str) -> Any:
        """调 ab_selector 选一个 model。"""
        # 兼容 ORM 行（带 .strategy 是 str）和 ABRuleConfig
        rules: list[ABRuleConfig] = []
        for r in self.ab_rules:
            if isinstance(r, ABRuleConfig):
                rules.append(r)
            else:
                rules.append(ABRuleConfig.from_orm(r))
        return select_model_by_ab(
            user_id=user_id, target=target, rules=rules, all_models=self.all_models
        )

    # ------------------------------------------------------------------ #
    # operation 工厂：把 model 传进 client                               #
    # ------------------------------------------------------------------ #

    def _build_chat_op(
        self,
        messages: Sequence[ChatMessage],
        temperature: float,
        max_tokens: int,
        stream: bool,
    ):
        """构造 ``async def op(model) -> ChatResponse``。"""

        async def op(model: Any) -> ChatResponse:
            client = self._client_for(model)
            return await client.chat(
                messages=list(messages),
                model=getattr(model, "model_name", str(getattr(model, "id", "?"))),
                temperature=temperature,
                max_tokens=max_tokens,
                stream=stream,
            )

        return op

    def _build_embed_op(self, texts: Sequence[str]):
        """构造 ``async def op(model) -> EmbedResponse``。"""

        async def op(model: Any) -> EmbedResponse:
            client = self._client_for(model)
            return await client.embed(
                texts=list(texts),
                model=getattr(model, "model_name", str(getattr(model, "id", "?"))),
            )

        return op

    # ------------------------------------------------------------------ #
    # 内部：根据 model 拿 client                                        #
    # ------------------------------------------------------------------ #

    def _client_for(self, model: Any) -> UnifiedModelClient:
        """根据 model 解析 provider，再调 get_client_fn 拿 client。"""
        provider = self._resolve_provider(model)
        provider_id = getattr(provider, "id", None)
        if provider_id is None:
            raise ValueError(
                f"provider for model {getattr(model, 'model_name', '?')!r} has no .id"
            )
        key = self.default_keys.get(provider_id)
        if key is None:
            raise ValueError(
                f"no decrypted key for provider id={provider_id!r}; "
                f"known providers: {list(self.default_keys.keys())}"
            )
        return self.get_client_fn(provider, key)

    def _resolve_provider(self, model: Any) -> Any:
        """根据 model 找到对应的 provider 对象。

        约定：model-like 需要带 ``provider`` 字段（直接引用 provider 对象）。
        如果只带 ``provider_id``，说明外部环境（DB loader）会先把 provider 装到 model 上。
        """
        provider = getattr(model, "provider", None)
        if provider is not None:
            return provider
        raise ValueError(
            f"model {getattr(model, 'model_name', '?')!r} has no .provider reference; "
            "ensure DB loader attaches provider to model before passing to router"
        )
