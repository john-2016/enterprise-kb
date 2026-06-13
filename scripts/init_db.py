"""Initialize the database."""
import asyncio
from pathlib import Path

from backend.config import settings
from backend.database import Base, init_db, get_engine


async def main():
    data_dir = Path(settings.DATA_DIR)
    data_dir.mkdir(parents=True, exist_ok=True)

    await init_db(settings.DATABASE_URL)
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    print(f"✅ Database initialized at {settings.DATABASE_URL}")


if __name__ == "__main__":
    asyncio.run(main())
