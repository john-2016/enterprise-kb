"""ABTestRule ORM 测试。"""
import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from backend.models import Base
from backend.models.ab_test import ABTestRule


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as s:
        yield s
    await engine.dispose()


def make_rule(**overrides):
    defaults = dict(
        name="default_rule",
        enabled=True,
        strategy="user_hash_mod",
        target="chat",
        config={"mod": 3, "mapping": {"0": "a", "1": "b", "2": "c"}},
    )
    defaults.update(overrides)
    return ABTestRule(**defaults)


async def test_create_rule_user_hash_mod(session):
    r = make_rule(name="chat_test")
    session.add(r)
    await session.commit()

    found = await session.get(ABTestRule, r.id)
    assert found is not None
    assert found.strategy == "user_hash_mod"
    assert found.config["mod"] == 3
    assert found.target == "chat"
    assert found.enabled is True


async def test_create_rule_random_weight(session):
    r = make_rule(
        name="rw_test",
        strategy="random_weight",
        config={"weights": {"m3": 0.7, "deepseek": 0.3}},
    )
    session.add(r)
    await session.commit()

    found = await session.get(ABTestRule, r.id)
    assert found.strategy == "random_weight"
    assert abs(found.config["weights"]["m3"] - 0.7) < 1e-9


async def test_rule_can_be_disabled(session):
    r = make_rule(name="off", enabled=False)
    session.add(r)
    await session.commit()
    assert (await session.get(ABTestRule, r.id)).enabled is False


async def test_rule_target_chat_or_embedding(session):
    r_chat = make_rule(name="t_chat", target="chat")
    r_emb = make_rule(name="t_emb", target="embedding")
    session.add_all([r_chat, r_emb])
    await session.commit()

    from sqlalchemy import select
    rules = (await session.execute(select(ABTestRule))).scalars().all()
    targets = {r.name: r.target for r in rules}
    assert targets["t_chat"] == "chat"
    assert targets["t_emb"] == "embedding"
