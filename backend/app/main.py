"""ForenSight FastAPI application entrypoint.

Wires probes, security middleware, rate limiting, notifications, and the
`/metrics` scrape endpoint that `infra/observability/prometheus.yml` targets.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

from app.agent.router import router as agent_router
from app.analysis.router import router as analysis_router
from app.auth.middleware import CSRFMiddleware, SecurityHeadersMiddleware
from app.auth.ratelimit import (
    FallbackRateLimiter,
    InMemoryRateLimiter,
    RedisRateLimiter,
)
from app.auth.router import router as auth_router
from app.capabilities import get_capabilities
from app.config import Settings, get_settings
from app.domain.router import router as cases_router
from app.health import gather_readiness
from app.observability import MetricsMiddleware, configure_logging, metrics_router

logger = logging.getLogger("forensight")


def _constraint_detail(exc: IntegrityError) -> str:
    """The violated constraint's name, when the driver exposes one.

    Naming the constraint tells the caller what collided — case number, evidence
    id, idempotency key — instead of only that "something did".

    asyncpg puts the name on its own error, but SQLAlchemy wraps that in its own
    `IntegrityError`, so it is reached by unwrapping `.orig` first. SQLite exposes
    no name at all, hence the generic fallback — better than echoing a driver
    exception class back to the client.
    """
    seen: list[Any] = []
    node: Any = exc
    # Walk the cause chain rather than checking one level: asyncpg's error may be
    # the `.orig` of SQLAlchemy's wrapper, or wrapped again depending on version.
    for _ in range(5):
        node = getattr(node, "orig", None) or getattr(node, "__cause__", None)
        if node is None or node in seen:
            break
        seen.append(node)
        name = getattr(node, "constraint_name", None)
        if name:
            return str(name)
    return "a unique constraint"


class MissingSecretKeyError(RuntimeError):
    """Raised when SECRET_KEY is not provided. The app must not boot without it."""


def _enforce_secret_key(settings: Settings) -> None:
    if not settings.secret_key or not settings.secret_key.strip():
        raise MissingSecretKeyError(
            "SECRET_KEY is not set. Refusing to start. "
            "Set it via environment/.env (see .env.example)."
        )


def _redis_client(settings: Settings) -> Any:
    """Construct a lazy Redis client, or None if the library is unusable.

    The client is not pinged here: `from_url` connects lazily and there is no
    running event loop at import time, so any "is it up?" probe would either be
    a lie or an error. Resilience is handled per-call instead — see
    FallbackRateLimiter and the try/except around notification publishing.
    """
    try:
        import redis.asyncio as aioredis

        return aioredis.from_url(settings.redis_url)  # type: ignore[no-untyped-call]
    except Exception as exc:  # noqa: BLE001 - degrade, do not fail boot
        logger.warning("Redis client unavailable (%s); using in-process fallback", exc)
        return None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Validate critical configuration at startup.
    _enforce_secret_key(app.state.settings)
    logger.info(
        "ForenSight %s starting (env=%s)",
        app.state.settings.app_name,
        app.state.settings.environment,
    )
    yield
    # Release pooled connections; without this gunicorn workers leak sockets
    # across every reload and the Redis client is never closed.
    client = getattr(app.state, "redis", None)
    if client is not None:
        # Shutdown must never raise; a dead Redis connection is not an error
        # worth failing a deploy over.
        with contextlib.suppress(Exception):
            await client.aclose()
    from app.db import engine

    await engine.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    # Fail fast even before the server starts serving.
    _enforce_secret_key(settings)
    configure_logging(settings)

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        docs_url="/docs",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.settings = settings

    # Rate limiter + notifications share one Redis connection when it is
    # actually reachable; both degrade to in-process implementations together.
    if getattr(app.state, "rate_limiter", None) is None:
        client = _redis_client(settings)
        app.state.redis = client
        if client is None:
            app.state.rate_limiter = InMemoryRateLimiter()
        else:
            # Wrap so a Redis outage falls back per-call rather than leaving
            # every endpoint unlimited or 500-ing the whole API.
            app.state.rate_limiter = FallbackRateLimiter(
                RedisRateLimiter(client), InMemoryRateLimiter()
            )

    # CORS: the Next.js frontend runs on a different origin. Credentials are
    # allowed because auth also works via cookies, so the browser must be
    # permitted to send them. Origins are explicit — never "*" with credentials.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-CSRF-Token"],
    )

    # Security middleware: headers on every response (T1.6) + CSRF (T1.5).
    app.add_middleware(CSRFMiddleware)
    app.add_middleware(
        SecurityHeadersMiddleware,
        hsts=(settings.environment.lower() == "production"),
    )
    app.add_middleware(MetricsMiddleware)

    from app.domain import router as _domain_router
    from app.domain.notifications import InMemoryNotifier, RedisNotifier

    if getattr(_domain_router, "_NOTIFIER", None) is None:
        _domain_router._NOTIFIER = (
            RedisNotifier(app.state.redis) if app.state.redis is not None else InMemoryNotifier()
        )

    # Register analyzers and agent tools at construction, not import time, so
    # tests can reset the registries without the app repopulating them, and so
    # GET /agent/tools reflects the real catalogue rather than an empty registry.
    from app.agent.builtin import register_all as register_agent_tools
    from app.analysis.registry import register_all as register_analyzers

    register_analyzers()
    register_agent_tools()

    app.include_router(auth_router)
    app.include_router(cases_router)
    app.include_router(analysis_router)
    app.include_router(agent_router)
    app.include_router(metrics_router)

    # A unique-constraint violation is a client mistake, not a server fault.
    #
    # Without this, creating a case whose number already existed returned **500**,
    # which is wrong twice over: it blames the server for the caller's duplicate,
    # and it leaks a stack trace through the 500 body. Found by creating the same
    # case number twice against the running stack.
    #
    # Registered app-wide rather than per-route so every unique constraint gets
    # the same treatment, including ones added by later phases.
    @app.exception_handler(IntegrityError)
    async def _integrity_error(request: Request, exc: IntegrityError) -> JSONResponse:
        detail = _constraint_detail(exc)
        logger.warning(
            "integrity constraint violated on %s %s: %s", request.method, request.url.path, detail
        )
        return JSONResponse(
            status_code=409,
            content={"detail": f"conflicts with an existing record: {detail}"},
        )

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
                s.name: {"ok": s.ok, **({"detail": s.detail} if s.detail else {})} for s in statuses
            },
        }
        return JSONResponse(status_code=200 if ready else 503, content=payload)

    @app.get("/capabilities", tags=["health"])
    async def capabilities() -> dict[str, object]:
        """Report analyzer capabilities; GPU modules degrade cleanly (T0.5).

        Built from the analysis registry, so the list is the shipped analyzers
        rather than a hand-maintained copy that can drift from the code.
        """
        caps = get_capabilities(app.state.settings)
        return {
            "gpu_available": app.state.settings.gpu_available,
            "capabilities": [
                {
                    "name": c.name,
                    "available": c.available,
                    "requires_gpu": c.requires_gpu,
                    "version": c.version,
                    **({"reason": c.reason} if c.reason else {}),
                }
                for c in caps
            ],
        }

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
