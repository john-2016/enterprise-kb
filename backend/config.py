"""
Application configuration via pydantic-settings.
Settings are loaded from environment variables and a .env file.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment / .env files."""

    # MiniMax API
    MINIMAX_API_KEY: str = ""
    MINIMAX_CN_API_KEY: str = ""  # 兼容两种变量名

    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///./data/kb.db"

    # JWT — 启动时由 validate_security_settings() 强制校验
    # 默认值故意取短（<32 字符）以触发长度校验，强制用户设置
    JWT_SECRET_KEY: str = "INSECURE_DEFAULT"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRATION_MINUTES: int = 60
    JWT_ISSUER: str = "enterprise-kb"
    JWT_AUDIENCE: str = "api"

    # Server
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    DATA_DIR: str = str(Path(__file__).resolve().parent.parent / "data")
    UPLOAD_MAX_SIZE: int = 50 * 1024 * 1024  # 50MB

    # CORS — 逗号分隔的允许来源
    CORS_ALLOWED_ORIGINS: str = "http://localhost:8000,http://127.0.0.1:8000,http://localhost:3000,http://127.0.0.1:3000"
    CORS_ALLOW_CREDENTIALS: bool = True

    # Environment (dev/staging/prod)
    ENV: str = "development"
    ALLOW_REGISTRATION: bool = False  # 默认禁止公开注册

    # Vector store
    VECTOR_DIMENSION: int = 1536
    VECTOR_TOP_K: int = 5

    # Encryption (Fernet) — 用于加密 model_providers.api_key_enc
    # 不强制长度：crypto.py 在使用时再校验（dev 环境方便测试）
    ENCRYPTION_KEY: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
    )


settings = Settings()


# ---------------------------------------------------------------------------
# Security validation — refuse to boot in production with weak defaults
# ---------------------------------------------------------------------------

_DEFAULT_JWT_SECRET = "INSECURE_DEFAULT"


def validate_security_settings() -> None:
    """校验关键安全设置；不安全则直接抛错拒绝启动。

    仅在 ENV=production 时强制执行；dev/staging 仅警告。
    """
    logger = logging.getLogger(__name__)
    is_prod = settings.ENV.lower() == "production"

    problems: list[str] = []

    # JWT secret
    if settings.JWT_SECRET_KEY == _DEFAULT_JWT_SECRET:
        msg = "JWT_SECRET_KEY is set to the default placeholder"
        problems.append(msg)
        if is_prod:
            logger.error(msg)
            sys.exit(f"FATAL: {msg}. Refusing to start in production.")
        else:
            logger.warning(f"{msg} (dev mode — allowed but insecure)")

    if len(settings.JWT_SECRET_KEY) < 32:
        msg = f"JWT_SECRET_KEY must be at least 32 chars (got {len(settings.JWT_SECRET_KEY)})"
        problems.append(msg)
        if is_prod:
            logger.error(msg)
            sys.exit(f"FATAL: {msg}.")
        else:
            logger.warning(msg)

    # CORS sanity
    if "*" in settings.CORS_ALLOWED_ORIGINS and settings.CORS_ALLOW_CREDENTIALS:
        msg = "CORS: allow_origins contains '*' with allow_credentials=True is forbidden by browsers"
        problems.append(msg)
        if is_prod:
            logger.error(msg)
            sys.exit(f"FATAL: {msg}.")
        else:
            logger.warning(msg)

    # DB
    if settings.DATABASE_URL.startswith("sqlite"):
        msg = "DATABASE_URL is sqlite — not for production"
        problems.append(msg)
        if is_prod:
            logger.error(msg)
            sys.exit(f"FATAL: {msg}.")
        else:
            logger.warning(msg)

    if problems:
        logger.info(f"Security validation: {len(problems)} issue(s) found, ENV={settings.ENV}")
