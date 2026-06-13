"""Database configuration and declarative base."""

from sqlalchemy.ext.asyncio import AsyncAttrs, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase


class Base(AsyncAttrs, DeclarativeBase):
    """SQLAlchemy 2.0 declarative base for all ORM models.

    Inherits AsyncAttrs for async relationship loading support.
    """
    pass


# Default engine and session factory — overridden at application startup.
engine = None
AsyncSessionLocal = None


def get_engine():
    """Get the initialized engine. Raises if init_db() not called yet."""
    if engine is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return engine


async def init_db(database_url: str, **kwargs):
    """Initialise the async engine and session factory.

    Call once at application startup (e.g. inside a lifespan handler).
    """
    global engine, AsyncSessionLocal
    engine = create_async_engine(database_url, echo=False, **kwargs)
    AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_async_session():
    """Yield an async session for dependency injection (FastAPI)."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
