"""
Application configuration via pydantic-settings.
Settings are loaded from environment variables and a .env file.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment / .env files."""

    # MiniMax API
    MINIMAX_API_KEY: str = ""
    MINIMAX_CN_API_KEY: str = ""  # 兼容两种变量名

    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///./data/kb.db"

    # JWT
    JWT_SECRET_KEY: str = "change-me-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRATION_MINUTES: int = 60

    # Server
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    DATA_DIR: str = str(Path(__file__).resolve().parent.parent / "data")
    UPLOAD_MAX_SIZE: int = 50 * 1024 * 1024  # 50MB

    # Vector store
    VECTOR_DIMENSION: int = 1536
    VECTOR_TOP_K: int = 5

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
    )


settings = Settings()
