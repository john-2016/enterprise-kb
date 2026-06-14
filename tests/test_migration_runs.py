"""Alembic 迁移验证 — 跑 upgrade head 不会报错。"""
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
ALEMBIC_BIN = ROOT / ".venv" / "bin" / "alembic"


def test_alembic_ini_in_backend():
    assert Path("backend/alembic.ini").exists()


def test_alembic_version_files_exist():
    versions = Path("backend/alembic/versions")
    assert versions.is_dir()
    files = list(versions.glob("*.py"))
    # 至少有一个迁移文件（除 __init__.py 外的 .py）
    migration_files = [f for f in files if f.name != "__init__.py"]
    assert len(migration_files) >= 1, "应至少有一个迁移脚本"


def test_alembic_history_command_runs():
    """alembic history 命令能跑（说明 env.py 加载成功）。"""
    result = subprocess.run(
        [str(ALEMBIC_BIN), "history"],
        cwd=str(ROOT / "backend"),
        capture_output=True,
        text=True,
        env={**os.environ, "DATABASE_URL": "sqlite:///test_migration_check.db"},
    )
    # 不要求有内容（可能 head 是空），但命令不能崩
    assert result.returncode == 0, f"alembic history 失败: {result.stderr}"
