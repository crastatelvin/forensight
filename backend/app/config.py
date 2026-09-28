"""Application settings loaded from environment (Pydantic v2).

No secrets are hardcoded. The app refuses to boot if SECRET_KEY is unset
(enforced in `app.main`), satisfying test T0.4.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Core
    app_name: str = "ForenSight"
    environment: str = Field(default="development")
    debug: bool = Field(default=False)

    # Security — no default; must be provided via env.
    secret_key: str = Field(default="")

    # Datastores (used by /readyz in later wiring)
    database_url: str = Field(
        default="postgresql+asyncpg://forensight:forensight@postgres:5432/forensight"
    )
    redis_url: str = Field(default="redis://redis:6379/0")

    minio_endpoint: str = Field(default="minio:9000")
    minio_access_key: str = Field(default="")
    minio_secret_key: str = Field(default="")
    minio_secure: bool = Field(default=False)

    # Capability flags — allow clean degradation on CPU-only / android-dev.
    gpu_available: bool = Field(default=False)


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()
