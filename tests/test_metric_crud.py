"""ABTestMetric ORM 测试 — 记录每次 chat/embed 调用的实际表现。"""
import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from backend.models import Base
from backend.models.user import User
from backend.models.ab_test import ABTestMetric
from backend.models.provider import ModelProvider
from backend.models.model_config import ModelConfig


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
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
async def user_and_model(session):
    u = User(username="u1", email="u1@x.com", hashed_password="x", role="user")
    session.add(u)
    p = ModelProvider(name="p1", display_name="P1", provider_type="openai_compat",
                      api_base_url="x", api_key_enc=b"x")
    session.add(p)
    await session.flush()
    m = ModelConfig(provider_id=p.id, model_name="m1", display_name="M1", model_type="chat",
                    is_default_chat=True)
    session.add(m)
    await session.commit()
    return u, m


async def test_create_metric_basic(session, user_and_model):
    u, m = user_and_model
    metric = ABTestMetric(
        user_id=u.id, model_id=m.id, request_type="chat",
        latency_ms=850, input_tokens=234, output_tokens=412,
    )
    session.add(metric)
    await session.commit()

    found = await session.get(ABTestMetric, metric.id)
    assert found is not None
    assert found.latency_ms == 850
    assert found.feedback is None
    assert found.request_type == "chat"


async def test_metric_feedback_range_minus1_to_1(session, user_and_model):
    u, m = user_and_model
    for fb in [-1, 0, 1]:
        m_obj = ABTestMetric(
            user_id=u.id, model_id=m.id, request_type="chat", feedback=fb,
        )
        session.add(m_obj)
    await session.commit()
    assert True  # 不抛异常就过


async def test_metric_feedback_text_optional(session, user_and_model):
    u, m = user_and_model
    m_obj = ABTestMetric(
        user_id=u.id, model_id=m.id, request_type="chat",
        feedback=-1, feedback_text="回答不准确",
    )
    session.add(m_obj)
    await session.commit()

    found = await session.get(ABTestMetric, m_obj.id)
    assert found.feedback_text == "回答不准确"


async def test_metric_cascade_delete_user(session, user_and_model):
    """删 user 时级联删 metrics。"""
    u, m = user_and_model
    metric = ABTestMetric(user_id=u.id, model_id=m.id, request_type="chat")
    session.add(metric)
    await session.commit()

    await session.delete(u)
    await session.commit()

    from sqlalchemy import select
    remaining = (await session.execute(select(ABTestMetric))).scalars().all()
    assert len(remaining) == 0
