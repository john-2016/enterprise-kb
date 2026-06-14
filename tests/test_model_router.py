"""ModelRouter 单元测试。

覆盖：
- 无 AB 规则时，chat 用 default chat 模型
- primary 失败时降级到 secondary（用按 model 行为的 smart client 验证）
- embed 用 default embedding 模型
- 所有模型都失败抛 AllModelsFailedError
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from backend.core.errors import AllModelsFailedError, NonRetryableError, RetryableError
from backend.services.model_clients.base import ChatMessage, ChatResponse, EmbedResponse
from backend.services.model_router import ModelRouter


# ----------------------------- mock 工厂 ----------------------------- #


def _make_provider(provider_id: int, name: str, provider_type: str = "openai_compat"):
    return SimpleNamespace(
        id=provider_id,
        name=name,
        provider_type=provider_type,
        api_base_url="https://api.example.com",
    )


def _make_model(
    model_id: int,
    name: str,
    *,
    provider_id: int = 1,
    is_default_chat: bool = False,
    is_default_emb: bool = False,
    fallback_model_ids: list[int] | None = None,
):
    # model-like 必须带 .provider（router 用它找 client）
    # 简化：所有 model 用同一个 provider（provider_id=1）
    # 多 provider 的场景在 test_router_embed_resolves_default 里手工构造
    provider = _make_provider(provider_id, f"provider-{provider_id}")
    return SimpleNamespace(
        id=model_id,
        model_name=name,
        provider_id=provider_id,
        provider=provider,
        is_default_chat=is_default_chat,
        is_default_emb=is_default_emb,
        fallback_model_ids=list(fallback_model_ids or []),
    )


class FakeChatClient:
    """按 model.model_name 决定行为的可注入 chat client。"""

    def __init__(self, behavior_map: dict[str, str] | None = None, default: str = "ok",
                 content: str = "ok"):
        # behavior_map: {"primary": "nonretryable", "secondary": "ok"}
        self.behavior_map = behavior_map or {}
        self.default_behavior = default
        self.content = content
        self.calls: list[dict] = []

    async def chat(self, messages, model, temperature, max_tokens, stream=False):
        self.calls.append(
            {"model": model, "n_messages": len(messages), "temperature": temperature}
        )
        beh = self.behavior_map.get(model, self.default_behavior)
        if beh == "ok":
            return ChatResponse(content=self.content, input_tokens=10, output_tokens=5)
        if beh == "retryable":
            raise RetryableError("upstream 503")
        if beh == "nonretryable":
            raise NonRetryableError("upstream 400")
        raise RuntimeError(f"unknown behavior {beh}")


class FakeEmbedClient:
    def __init__(self, behavior: str = "ok"):
        self.behavior = behavior
        self.calls: list[dict] = []

    async def embed(self, texts, model):
        self.calls.append({"model": model, "n_texts": len(texts)})
        if self.behavior == "ok":
            return EmbedResponse(vectors=[[0.1, 0.2, 0.3]] * len(texts), model=model)
        if self.behavior == "retryable":
            raise RetryableError("embed 503")
        raise NonRetryableError("embed 400")


# ----------------------------- 1. chat 用 default ----------------------------- #


def test_router_chat_resolves_default():
    """无 AB 规则时，router 选 default chat 模型。"""
    primary = _make_model(1, "default-chat", is_default_chat=True)
    secondary = _make_model(2, "alt-chat")
    provider = _make_provider(1, "openai")

    chat_client = FakeChatClient(content="hello from default")

    router = ModelRouter(
        ab_rules=[],
        all_models=[primary, secondary],
        get_client_fn=lambda prov, key: chat_client,
        default_keys={provider.id: "sk-test"},
    )

    msgs = [ChatMessage(role="user", content="hi")]
    result = asyncio.run(
        router.chat(user_id=42, target="chat", messages=msgs, temperature=0.5, max_tokens=100)
    )

    assert isinstance(result, ChatResponse)
    assert result.content == "hello from default"
    assert chat_client.calls[0]["model"] == "default-chat"
    assert chat_client.calls[0]["temperature"] == 0.5


# ----------------------------- 2. primary 失败切到 secondary ----------------------------- #


def test_router_uses_fallback_on_failure():
    """primary 失败时，router 降级到 secondary 并成功返回。"""
    default = _make_model(0, "default-chat", is_default_chat=True)
    primary = _make_model(1, "primary", fallback_model_ids=[2])
    secondary = _make_model(2, "secondary")
    provider = _make_provider(1, "openai")

    # 用 AB 规则强制选 primary（而不是 default）
    ab_rule = SimpleNamespace(
        strategy="user_hash_mod",
        config={"mod": 1, "mapping": {"0": "primary"}},
        enabled=True,
        target="chat",
    )

    # 同一 client 按 model 决定行为
    chat_client = FakeChatClient(
        behavior_map={"primary": "nonretryable", "secondary": "ok"},
        content="from-secondary",
    )

    router = ModelRouter(
        ab_rules=[ab_rule],
        all_models=[default, primary, secondary],
        get_client_fn=lambda prov, key: chat_client,
        default_keys={provider.id: "sk-test"},
        max_retries=0,  # 不重试，更快
    )

    msgs = [ChatMessage(role="user", content="hi")]
    result = asyncio.run(
        router.chat(user_id=1, target="chat", messages=msgs, temperature=0.7, max_tokens=50)
    )

    # primary 被调 1 次（max_retries=0 → 1 attempt）→ 失败 → 切 secondary → 成功
    models_called = [c["model"] for c in chat_client.calls]
    assert models_called == ["primary", "secondary"]
    assert result.content == "from-secondary"


# ----------------------------- 3. embed 用 default ----------------------------- #


def test_router_embed_resolves_default():
    """embed 用 default embedding 模型。"""
    emb_model = _make_model(1, "default-emb", is_default_emb=True, provider_id=1)
    chat_model = _make_model(2, "default-chat", is_default_chat=True, provider_id=2)
    provider1 = _make_provider(1, "openai-emb")
    provider2 = _make_provider(2, "openai-chat")

    chat_client = FakeChatClient()
    embed_client = FakeEmbedClient()

    client_by_prov = {1: embed_client, 2: chat_client}

    router = ModelRouter(
        ab_rules=[],
        all_models=[emb_model, chat_model],
        get_client_fn=lambda prov, key: client_by_prov[prov.id],
        default_keys={provider1.id: "k1", provider2.id: "k2"},
    )

    result = asyncio.run(router.embed(user_id=10, texts=["hello", "world"]))

    assert isinstance(result, EmbedResponse)
    assert len(result.vectors) == 2
    assert embed_client.calls[0]["model"] == "default-emb"
    assert embed_client.calls[0]["n_texts"] == 2
    # chat client 不应被调用
    assert len(chat_client.calls) == 0


# ----------------------------- 4. 所有模型失败抛 AllModelsFailedError ----------------------------- #


def test_router_all_failed_raises():
    """所有模型都失败时抛 AllModelsFailedError，消息含所有尝试过的模型名。"""
    default = _make_model(0, "default-chat", is_default_chat=True)
    primary = _make_model(1, "primary", fallback_model_ids=[2])
    secondary = _make_model(2, "secondary")
    provider = _make_provider(1, "openai")

    # 用 AB 规则强制选 primary
    ab_rule = SimpleNamespace(
        strategy="user_hash_mod",
        config={"mod": 1, "mapping": {"0": "primary"}},
        enabled=True,
        target="chat",
    )

    chat_client = FakeChatClient(
        behavior_map={
            "primary": "nonretryable",
            "secondary": "nonretryable",
        },
    )

    router = ModelRouter(
        ab_rules=[ab_rule],
        all_models=[default, primary, secondary],
        get_client_fn=lambda prov, key: chat_client,
        default_keys={provider.id: "sk"},
        max_retries=0,
    )

    msgs = [ChatMessage(role="user", content="hi")]
    with pytest.raises(AllModelsFailedError) as excinfo:
        asyncio.run(
            router.chat(user_id=1, target="chat", messages=msgs, temperature=0.5, max_tokens=10)
        )
    msg = str(excinfo.value)
    assert "primary" in msg
    assert "secondary" in msg
