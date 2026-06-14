"""ModelProvider ORM 测试。"""
from types import SimpleNamespace
import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from backend.models import Base
from backend.models.provider import ModelProvider


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as s:
        yield s
    await engine.dispose()


def make_provider(**overrides):
    """构造测试用 provider，所有字段都给合理默认值。"""
    defaults = dict(
        name="default_name",
        display_name="Default Display Name",
        provider_type="openai_compat",
        api_base_url="https://api.example.com/v1",
        api_key_enc=b"encrypted_blob",
        enabled=True,
        is_builtin=False,
    )
    defaults.update(overrides)
    return ModelProvider(**defaults)


async def test_create_provider(session):
    p = make_provider(name="minimax", is_builtin=True)
    session.add(p)
    await session.commit()

    found = await session.get(ModelProvider, p.id)
    assert found is not None
    assert found.name == "minimax"
    assert found.is_builtin is True
    assert found.enabled is True
    assert found.provider_type == "openai_compat"


async def test_provider_name_unique(session):
    p1 = make_provider(name="dup", api_base_url="https://a/v1", api_key_enc=b"x")
    session.add(p1)
    await session.commit()

    p2 = make_provider(name="dup", api_base_url="https://b/v1", api_key_enc=b"y")
    session.add(p2)
    with pytest.raises(Exception):
        await session.commit()


async def test_provider_extra_config_default_empty_dict(session):
    p = make_provider(name="p1")
    session.add(p)
    await session.commit()
    assert p.extra_config == {} or p.extra_config is None


async def test_provider_can_be_disabled(session):
    p = make_provider(name="p_disabled", enabled=False)
    session.add(p)
    await session.commit()
    assert (await session.get(ModelProvider, p.id)).enabled is False


async def test_provider_timestamps_auto_set(session):
    p = make_provider(name="ts_test")
    session.add(p)
    await session.commit()
    assert p.created_at is not None
    assert p.updated_at is not None
