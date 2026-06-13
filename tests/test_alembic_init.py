"""Alembic 初始化验证 — 确保异步迁移环境就绪。"""

from pathlib import Path


def test_alembic_ini_exists():
    assert Path("backend/alembic.ini").exists(), "alembic.ini 应在 backend/ 下"


def test_alembic_env_uses_async_url():
    env = Path("backend/alembic/env.py").read_text()
    assert "run_async" in env, "env.py 应使用异步迁移"
    assert "async_engine_from_config" in env, "env.py 应使用 async_engine_from_config"


def test_alembic_versions_dir_exists():
    assert Path("backend/alembic/versions").is_dir(), "versions 目录应存在"
