"""Readiness checks for external dependencies (Postgres, Redis, MinIO).

Each check is best-effort and fast-failing. `/readyz` returns 200 only when
all configured dependencies are reachable (test T0.1).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from app.config import Settings


@dataclass(frozen=True)
class DependencyStatus:
    name: str
    ok: bool
    detail: str = ""


async def check_postgres(settings: Settings) -> DependencyStatus:
    try:
        import asyncpg

        # asyncpg wants a plain postgres:// DSN, not the SQLAlchemy +asyncpg form.
        dsn = settings.database_url.replace("+asyncpg", "")
        conn = await asyncio.wait_for(asyncpg.connect(dsn), timeout=3.0)
        try:
            await conn.execute("SELECT 1")
        finally:
            await conn.close()
        return DependencyStatus("postgres", True)
    except Exception as exc:  # noqa: BLE001 - report any failure as not-ready
        return DependencyStatus("postgres", False, str(exc))


async def check_redis(settings: Settings) -> DependencyStatus:
    try:
        import redis.asyncio as aioredis

        client: aioredis.Redis = aioredis.from_url(  # type: ignore[no-untyped-call]
            settings.redis_url
        )
        try:
            await asyncio.wait_for(client.ping(), timeout=3.0)
        finally:
            await client.aclose()
        return DependencyStatus("redis", True)
    except Exception as exc:  # noqa: BLE001
        return DependencyStatus("redis", False, str(exc))


async def check_minio(settings: Settings) -> DependencyStatus:
    if not settings.minio_endpoint:
        return DependencyStatus("minio", False, "endpoint not configured")
    try:
        from minio import Minio

        def _probe() -> None:
            client = Minio(
                settings.minio_endpoint,
                access_key=settings.minio_access_key or None,
                secret_key=settings.minio_secret_key or None,
                secure=settings.minio_secure,
            )
            # list_buckets forces an authenticated round-trip to the server.
            client.list_buckets()

        await asyncio.wait_for(asyncio.to_thread(_probe), timeout=3.0)
        return DependencyStatus("minio", True)
    except Exception as exc:  # noqa: BLE001
        return DependencyStatus("minio", False, str(exc))


async def gather_readiness(settings: Settings) -> list[DependencyStatus]:
    return list(
        await asyncio.gather(
            check_postgres(settings),
            check_redis(settings),
            check_minio(settings),
        )
    )
