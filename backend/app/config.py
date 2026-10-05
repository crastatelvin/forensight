"""Application settings loaded from environment (Pydantic v2).

No secrets are hardcoded. The app refuses to boot if SECRET_KEY is unset
(enforced in `app.main`), satisfying test T0.4.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

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

    # --- Auth / JWT (Phase 1) ---
    # Lifetimes in seconds.
    access_token_ttl: int = Field(default=900)  # 15 minutes
    refresh_token_ttl: int = Field(default=1209600)  # 14 days
    jwt_algorithm: str = Field(default="HS256")

    # Cookie flags for cookie-based auth flows.
    cookie_secure: bool = Field(default=True)  # override to False for local http
    cookie_domain: str = Field(default="")
    cookie_samesite: Literal["lax", "strict", "none"] = Field(default="lax")

    # Browser origins allowed to call the API with credentials. Explicit list,
    # never "*" — credentialed CORS rejects the wildcard anyway.
    cors_origins: list[str] = Field(default=["http://localhost:3000", "http://127.0.0.1:3000"])

    # Rate limiting (Redis-backed; in-memory fallback for tests).
    rate_limit_auth_max: int = Field(default=5)  # attempts
    rate_limit_auth_window: int = Field(default=60)  # seconds
    rate_limit_mutation_max: int = Field(default=60)
    rate_limit_mutation_window: int = Field(default=60)

    # Initial admin seed (password MUST come from env; never hardcoded — T1.7).
    seed_admin_email: str = Field(default="")
    seed_admin_password: str = Field(default="")

    # --- Evidence / AV (Phase 2) ---
    # If set, evidence uploads are scanned via clamd at this host (TCP 3310).
    clamav_host: str = Field(default="")

    # --- Analysis workers (Phase 4) ---
    # Queue backend for analysis jobs. Left unset in dev so submission and
    # execution still work via the inline /run endpoint.
    queue_url: str = Field(default="")

    # --- Agent / LLM (Phase 5) ---
    # `ollama` for a local model, `cloud` for the hosted default. Switching is
    # configuration only; no code change (T5.1).
    llm_provider: str = Field(default="")
    llm_model: str = Field(default="")
    llm_base_url: str = Field(default="")
    llm_api_key: str = Field(default="")
    llm_embed_model: str = Field(default="")
    #: Must match the pgvector column width in migration 0006, and the width the
    #: embedding model actually returns. A mismatch is rejected on write rather
    #: than silently degrading recall.
    embed_dim: int = Field(default=1024, ge=8, le=8192)

    # --- Reverse proxy ---
    # uvicorn/gunicorn trust X-Forwarded-For from these sources only. Rate
    # limiting keys on client IP, so trusting everything ("*") lets any caller
    # forge an IP and evade limits entirely. Default to loopback.
    forwarded_allow_ips: str = Field(default="127.0.0.1")

    # Number of gunicorn worker processes in the container.
    web_concurrency: int = Field(default=2, ge=1, le=32)


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()
