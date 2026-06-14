"""Alembic 初始化验证 — 确保异步迁移环境就绪。"""

from pathlib import Path


def test_alembic_ini_exists():
    assert Path("backend/alembic.ini").exists(), "alembic.ini 应在 backend/ 下"


def test_alembic_env_uses_target_metadata():
    """env.py 必须加载 Base.metadata 才能支持 autogenerate。"""
    env = Path("backend/alembic/env.py").read_text()
    assert "from backend.models import Base" in env, "env.py 必须 import Base"
    assert "target_metadata = Base.metadata" in env, "env.py 必须设置 target_metadata"


def test_alembic_versions_dir_exists():
    assert Path("backend/alembic/versions").is_dir(), "versions 目录应存在"
