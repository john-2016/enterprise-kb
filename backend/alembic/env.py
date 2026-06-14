"""Alembic 同步迁移环境。

使用同步 engine（兼容 SQLite 本地开发 + PostgreSQL 生产）。
从环境变量 ``DATABASE_URL`` 注入 URL（自动剥离 async driver 标记）。
"""
import os
import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool

from alembic import context

# 把项目根目录加进 sys.path，确保能 import backend.*
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# 引入项目 Base 以支持 autogenerate
from backend.models import Base  # noqa: E402

# this is the Alembic Config object
config = context.config

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Inject DB URL from environment if set (so we don't store secrets in alembic.ini)
_db_url = os.environ.get("DATABASE_URL", "")
if _db_url:
    # alembic 用同步 driver；asyncpg 替换成 psycopg2/psycopg
    sync_url = _db_url.replace("postgresql+asyncpg://", "postgresql://")
    sync_url = sync_url.replace("sqlite+aiosqlite://", "sqlite://")
    config.set_main_option("sqlalchemy.url", sync_url)

# 项目所有 model 的 metadata，用于 autogenerate
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode (sync)."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
