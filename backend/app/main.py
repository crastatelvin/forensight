"""ForenSight FastAPI application entrypoint.

Phase 0 scope: app factory, liveness (`/healthz`) and readiness (`/readyz`)
probes, and the boot-time SECRET_KEY guard (test T0.4).
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.config import Settings, get_settings
from app.health import gather_readiness


class MissingSecretKeyError(RuntimeError):
    """Raised when SECRET_KEY is not provided. The app must not boot without it."""


def _enforce_secret_key(settings: Settings) -> None:
    if not settings.secret_key or not settings.secret_key.strip():
        raise MissingSecretKeyError(
            "SECRET_KEY is not set. Refusing to start. "
            "Set it via environment/.env (see .env.example)."
        )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Validate critical configuration at startup.
    _enforce_secret_key(app.state.settings)
    yield


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    # Fail fast even before the server starts serving.
    _enforce_secret_key(settings)

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        docs_url="/docs",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )
    app.state.settings = settings

    @app.get("/healthz", tags=["health"])
    async def healthz() -> dict[str, str]:
        """Liveness: process is up. No dependency checks."""
        return {"status": "ok"}

    @app.get("/readyz", tags=["health"])
    async def readyz() -> JSONResponse:
        """Readiness: 200 only when all deps reachable (test T0.1)."""
        statuses = await gather_readiness(app.state.settings)
        ready = all(s.ok for s in statuses)
        payload = {
            "ready": ready,
            "dependencies": {
                s.name: {"ok": s.ok, **({"detail": s.detail} if s.detail else {})}
                for s in statuses
            },
        }
        return JSONResponse(status_code=200 if ready else 503, content=payload)

    return app


# Uvicorn/Gunicorn entrypoint: `uvicorn app.main:app`.
# Lazily constructed via PEP 562 so importing `create_app`/exceptions in tests
# does not trigger the SECRET_KEY guard at import time.
_app: FastAPI | None = None


def __getattr__(name: str) -> object:
    global _app
    if name == "app":
        if _app is None:
            _app = create_app()
        return _app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
