"""ModelConfig ORM 测试。"""
import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import select

from backend.models import Base
from backend.models.provider import ModelProvider
from backend.models.model_config import ModelConfig


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    # SQLite 默认不强制 FK；测试需要显式打开
    from sqlalchemy import event
    @event.listens_for(engine.sync_engine, "connect")
    def _fk_on(dbapi_conn, _):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as s:
        yield s
    await engine.dispose()


@pytest.fixture
async def provider(session):
    p = ModelProvider(
        name="minimax",
        display_name="MiniMax",
        provider_type="openai_compat",
        api_base_url="https://api.minimaxi.com/v1",
        api_key_enc=b"encrypted",
        is_builtin=True,
    )
    session.add(p)
    await session.commit()
    return p


def make_model_config(provider_id, **overrides):
    defaults = dict(
        provider_id=provider_id,
        model_name="default_model",
        display_name="Default Model",
        model_type="chat",
        context_window=128000,
        enabled=True,
    )
    defaults.update(overrides)
    return ModelConfig(**defaults)


async def test_create_model_config(session, provider):
    m = make_model_config(provider.id, model_name="MiniMax-M3", is_default_chat=True)
    session.add(m)
    await session.commit()

    found = await session.get(ModelConfig, m.id)
    assert found is not None
    assert found.model_name == "MiniMax-M3"
    assert found.is_default_chat is True
    assert found.provider_id == provider.id


async def test_unique_provider_model_name(session, provider):
    """同一 provider 下 model_name 唯一。"""
    m1 = make_model_config(provider.id, model_name="dup")
    session.add(m1)
    await session.commit()

    m2 = make_model_config(provider.id, model_name="dup")
    session.add(m2)
    with pytest.raises(Exception):
        await session.commit()


async def test_default_chat_uniqueness_helper(session, provider):
    """is_default_chat 全表唯一 — 通过 DB UniqueConstraint 强制。"""
    m1 = make_model_config(provider.id, model_name="m1", is_default_chat=True)
    m2 = make_model_config(provider.id, model_name="m2", is_default_chat=True)
    session.add_all([m1, m2])
    with pytest.raises(Exception):
        await session.commit()


async def test_model_type_chat_or_embed(session, provider):
    m_chat = make_model_config(provider.id, model_name="chat_model", model_type="chat")
    m_emb = make_model_config(provider.id, model_name="emb_model", model_type="embedding")
    session.add_all([m_chat, m_emb])
    await session.commit()

    results = (await session.execute(select(ModelConfig))).scalars().all()
    types = {m.model_name: m.model_type for m in results}
    assert types["chat_model"] == "chat"
    assert types["emb_model"] == "embedding"


async def test_model_can_be_disabled(session, provider):
    m = make_model_config(provider.id, model_name="m_off", enabled=False)
    session.add(m)
    await session.commit()
    assert (await session.get(ModelConfig, m.id)).enabled is False


async def test_cascade_delete_from_provider(session, provider):
    """删 provider 时级联删 models。"""
    m1 = make_model_config(provider.id, model_name="c1")
    m2 = make_model_config(provider.id, model_name="c2")
    session.add_all([m1, m2])
    await session.commit()

    await session.delete(provider)
    await session.commit()

    remaining = (await session.execute(select(ModelConfig))).scalars().all()
    assert len(remaining) == 0
