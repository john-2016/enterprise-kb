"""Seed test data."""
import asyncio
from backend.config import settings
from backend import database
from backend.models import User
from backend.services.auth_service import register_user


async def main():
    await database.init_db(settings.DATABASE_URL)
    engine = database.get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(database.Base.metadata.create_all)

    async with database.AsyncSessionLocal() as session:
        # Create admin
        admin = await register_user(
            session, "admin", "admin@example.com", "admin123", role="admin"
        )
        print(f"✅ Admin user created: {admin['username']} / admin123")

        # Create editor
        editor = await register_user(
            session, "editor", "editor@example.com", "editor123", role="editor"
        )
        print(f"✅ Editor user created: {editor['username']} / editor123")

        # Create viewer
        viewer = await register_user(
            session, "viewer", "viewer@example.com", "viewer123", role="viewer"
        )
        print(f"✅ Viewer user created: {viewer['username']} / viewer123")


if __name__ == "__main__":
    asyncio.run(main())
