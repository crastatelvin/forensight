"""Analysis worker (Phase 4).

Dramatiq rather than Celery: the job shape is fire-and-forget with retry on
transient failure, and Dramatiq does that with far less machinery (no beat
scheduler, no result backend, no chaining). The plan allows Celery/RQ/Dramatiq;
switching means replacing this file and `app/analysis/queue.py`.

The broker is built explicitly from settings rather than through dramatiq's
`dramatiq_broker__url__*` environment convention — one source of truth for
configuration, and no silent default-to-RAMBroker when an env var is misspelled.

Run with:  dramatiq workers.dramatiq_worker --processes N --threads N
"""

from __future__ import annotations

import asyncio
import uuid

import dramatiq
from app.analysis.executor import execute_job
from app.analysis.registry import register_all
from app.analysis.repository import get_job
from app.config import get_settings
from app.db import SessionLocal
from dramatiq.brokers.redis import RedisBroker

settings = get_settings()

if not settings.queue_url:
    # Default to Redis on the standard port rather than dramatiq's in-memory
    # broker: a worker silently processing from RAM looks healthy while doing
    # nothing useful.
    broker: dramatiq.Broker = RedisBroker(url="redis://redis:6379/1")  # type: ignore[no-untyped-call]
else:
    broker = RedisBroker(url=settings.queue_url)  # type: ignore[no-untyped-call]

dramatiq.set_broker(broker)
register_all()


@dramatiq.actor(
    # execute_job records analysis failures as `failed`, so a retry here only
    # helps for infrastructure faults (DB blip, object-store timeout).
    max_retries=3,
    min_backoff=2_000,
    max_backoff=60_000,
    time_limit=600_000,
)
def run_analysis_job(job_id: str) -> None:
    """Execute one job. Never raises for an analysis failure (T4.2)."""
    _run_job_sync(job_id)


def _run_job_sync(job_id: str) -> None:
    """Bridge the sync worker to the async data layer.

    A fresh event loop per job rather than a shared one: the worker is
    single-threaded, and a per-job loop stops a poisoned connection from leaking
    into the next job's session.
    """

    async def _main() -> None:
        async with SessionLocal() as session:
            job = await get_job(session, uuid.UUID(job_id))
            if job is None:
                # Job was removed (case cascade delete). Acking is correct.
                return
            await execute_job(
                session,
                job,
                settings=get_settings(),
                gpu_available=get_settings().gpu_available,
            )

    asyncio.run(_main())
