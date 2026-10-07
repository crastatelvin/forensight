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
from app import tracing
from app.analysis.executor import execute_job
from app.analysis.registry import register_all
from app.analysis.repository import get_job
from app.config import get_settings
from app.db import create_engine
from dramatiq.brokers.redis import RedisBroker
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

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
def run_analysis_job(job_id: str, *, traceparent: str = "") -> None:
    """Execute one job. Never raises for an analysis failure (T4.2).

    `traceparent` arrives in the message options set by `enqueue_analysis_job`, which
    is what makes this span a **child** of the API's rather than a second root. The
    parameter is a plain string rather than the message options dict because Dramatiq
    has no per-argument options mechanism — every option would otherwise become an
    argument the actor does not otherwise need.
    """
    _run_job_sync(job_id, traceparent=traceparent)


def _run_job_sync(job_id: str, *, traceparent: str = "") -> None:
    """Bridge the sync worker to the async data layer.

    A fresh event loop *and a fresh engine* per job.

    The engine part is load-bearing, and it was a live bug: `app.db.engine` is a
    module-level pool, and a pooled asyncpg connection is bound to the event loop
    that first used it. Running `asyncio.run()` per job creates a new loop each
    time, so every job after the first checked out a connection belonging to a
    dead loop and died with `got Future ... attached to a different loop`. The
    first job succeeded, which is what made it look intermittent.

    Two costs, stated rather than hidden:
    - A TCP+TLS handshake per job to Postgres. At this workload that is irrelevant;
      a long-lived loop with `NullPool` would be the fix if it ever were not.
    - Re-creating the engine means a fresh pool each time, so there is no
      connection reuse across jobs. That is the trade for correctness, and the
      alternative (`NullPool` on the shared engine) would silently do the same
      thing more confusingly.
    """

    # Parsed *outside* the event loop and rejected if malformed: an unparseable parent
    # must start a fresh trace rather than be guessed at, because attaching the work to
    # the wrong trace is worse than losing the link.
    parent = tracing.TraceParent.parse(traceparent)

    async def _main() -> None:
        engine = create_engine()
        maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        job_settings = get_settings()
        try:
            async with maker() as session:
                job = await get_job(session, uuid.UUID(job_id))
                if job is None:
                    # Job was removed (case cascade delete). Acking is correct.
                    return
                # A ModelStore, so modules with pinned weights can load them. Without
                # this every `object-detect` and `face-compare` job failed with
                # "needs a verified model but no store was provided" -- the modules
                # worked in tests and were unusable in the deployed worker, because
                # nothing passed a store.
                from app.analysis.models_registry import ModelStore

                # The worker span, parented to the API's span across the queue
                # boundary. Recording it is best-effort — a trace that can fail a job
                # would be worse than no trace — so `record` swallows its own errors.
                with tracing.span(
                    "worker.execute_job",
                    parent=parent,
                    case_id=job.case_id,
                ) as worker_span:
                    outcome = await execute_job(
                        session,
                        job,
                        settings=job_settings,
                        gpu_available=job_settings.gpu_available,
                        model_store=ModelStore(job_settings),
                    )
                    await tracing.finish(
                        worker_span,
                        session,
                        status="ok" if job.status == "done" else job.status,
                        job_id=str(job.id),
                        module=job.module,
                        attempts=job.attempts,
                        trace_id=worker_span.trace_id,
                    )
                _ = outcome
        finally:
            await engine.dispose()

    asyncio.run(_main())
