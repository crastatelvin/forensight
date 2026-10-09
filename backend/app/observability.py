"""Logging + Prometheus metrics (P10.B1 groundwork).

`infra/observability/prometheus.yml` scrapes `/metrics` on this service, so the
endpoint has to exist or Prometheus records the target as permanently down.
Metrics are deliberately few: request rate, error rate and latency per route,
which is what the SLOs in the plan actually need.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from sqlalchemy import text
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response as StarletteResponse

from app.config import Settings

#: Must match `archive_timeout` in infra/postgres/conf.d/10-wal-archiving.conf and the
#: `-c archive_timeout=300` flag in docker-compose.yml. Named here because a silent drift
#: between them would make the stall alert fire at the wrong time with no symptom.
WAL_ARCHIVE_TIMEOUT_SECONDS = 300

logger = logging.getLogger(__name__)

REQUESTS = Counter(
    "forensight_http_requests_total",
    "HTTP requests handled",
    ["method", "route", "status"],
)
LATENCY = Histogram(
    "forensight_http_request_duration_seconds",
    "Request latency in seconds",
    ["method", "route"],
    # Buckets tuned for an API whose slow path is a large evidence download.
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 15.0, 60.0),
)
EVIDENCE_INGESTED = Counter(
    "forensight_evidence_ingested_total",
    "Evidence objects accepted (deduped uploads counted too)",
)
ANALYSIS_JOBS = Counter(
    "forensight_analysis_jobs_total",
    "Analysis jobs by terminal state",
    ["module", "outcome"],
)

#: Write-ahead-log archiving, which is what makes point-in-time recovery possible (P10.B2).
#:
#: **These exist because the failure is invisible without them.** Measured on 2026-10-09
#: with `archive_mode = on` and every setting correct: the archive directory was owned by
#: root while the server runs as uid 999, so `archive_command` failed on every segment.
#: The container was healthy, the settings page showed archiving enabled, and the logs were
#: empty — the only evidence was `pg_stat_archiver.failed_count`. A point-in-time recovery
#: that cannot reach its recovery point fails at the moment someone needs it.
#:
#: `failed_total` is the raw monotonic counter, kept because it is the record. The alert
#: is written against `failed_delta` instead: a raw counter that has ever been non-zero
#: stays non-zero forever, so alerting on it fires permanently on any cluster that has
#: recovered from a transient failure — which is how an alert gets muted and then missed.
WAL_ARCHIVE_FAILED = Gauge(
    "forensight_wal_archive_failed_total",
    "WAL segments the archiver has failed to archive, since server start",
)
WAL_ARCHIVE_FAILED_DELTA = Gauge(
    "forensight_wal_archive_failed_delta",
    "WAL archive failures since the previous scrape. Alert on THIS, not the counter: "
    "the counter never decreases, so alerting on it fires forever once anything has "
    "ever failed.",
)
WAL_ARCHIVED_SEGMENTS = Gauge(
    "forensight_wal_archived_segments_total",
    "WAL segments successfully archived since server start",
)
WAL_ARCHIVE_LAST_SUCCESS = Gauge(
    "forensight_wal_last_archived_timestamp_seconds",
    "Unix time of the last successful WAL archive, or 0 if none has succeeded",
)
#: `archive_timeout` is 300s in infra/postgres/conf.d/10-wal-archiving.conf, so a segment
#: should appear within ~5 minutes on an idle system. Two intervals of slack before
#: calling it stalled, which tolerates a missed scrape without firing on a quiet cluster.
WAL_ARCHIVE_STALLED = Gauge(
    "forensight_wal_archive_stalled",
    "1 when WAL archiving has not succeeded for more than twice archive_timeout",
)


async def observe_wal_archiver(session: Any) -> None:
    """Read `pg_stat_archiver` and publish it. Never raises.

    Called from the same place as the other collectors. A metrics scrape must not be able
    to fail the thing it measures, so a database error here leaves the last values in
    place rather than raising into the request path.
    """
    previous = int(WAL_ARCHIVE_FAILED_DELTA._value.get())  # noqa: SLF001 - prometheus_client has no public read
    try:
        row = (
            await session.execute(
                text(
                    "select archived_count, failed_count,"
                    " coalesce(extract(epoch from last_archived_time), 0)"
                    " from pg_stat_archiver"
                )
            )
        ).one()
    except Exception:  # noqa: BLE001 - a scrape must not fail the app
        return
    archived, failed, last_ok = int(row[0]), int(row[1]), float(row[2])
    WAL_ARCHIVED_SEGMENTS.set(archived)
    WAL_ARCHIVE_FAILED.set(failed)
    WAL_ARCHIVE_FAILED_DELTA.set(max(0, failed - previous))
    WAL_ARCHIVE_LAST_SUCCESS.set(last_ok)
    WAL_ARCHIVE_STALLED.set(
        1.0 if (last_ok == 0 or time.time() - last_ok > 2 * WAL_ARCHIVE_TIMEOUT_SECONDS) else 0.0
    )


def configure_logging(settings: Settings) -> None:
    """Single structured-ish log config, applied once at app construction."""
    level = logging.DEBUG if settings.debug else logging.INFO
    root = logging.getLogger()
    if root.handlers:
        # Already configured (e.g. by uvicorn); don't stack duplicate handlers.
        root.setLevel(level)
        return
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)-8s %(name)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    )
    root.addHandler(handler)
    root.setLevel(level)
    # These are chatty at INFO and drown out request logs.
    for noisy in ("uvicorn.access", "botocore", "urllib3", "minio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


class MetricsMiddleware(BaseHTTPMiddleware):
    """Record request count/latency keyed by *route template*.

    Keying on the matched path (`/cases/{case_id}`) rather than the raw path
    keeps cardinality bounded — otherwise every evidence id becomes its own time
    series and Prometheus falls over.
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[StarletteResponse]]
    ) -> StarletteResponse:
        start = time.perf_counter()
        response = await call_next(request)
        route = request.scope.get("route")
        template = getattr(route, "path", None) or "unmatched"
        elapsed = time.perf_counter() - start
        REQUESTS.labels(
            method=request.method, route=template, status=str(response.status_code)
        ).inc()
        LATENCY.labels(method=request.method, route=template).observe(elapsed)
        return response


metrics_router = APIRouter(tags=["observability"])


@metrics_router.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


async def wal_archiver_poller(interval: float = 30.0) -> None:
    """Poll `pg_stat_archiver` on a background loop.

    A background loop rather than a read inside the `/metrics` handler, for two reasons:
    a scrape must not depend on the database being reachable at that instant (a scrape
    during a failover would otherwise report the database as unhealthy *and* take as long
    as the connection timeout), and the delta calculation needs a previous value that
    survives between scrapes.

    30s is well inside the 300s `archive_timeout`, so `..._archive_stalled` goes to 1
    within ~5 minutes of archiving breaking rather than at the next archive timeout.
    """
    from app.db import SessionLocal  # noqa: PLC0415 - avoids an import cycle

    while True:
        try:
            async with SessionLocal() as session:
                await observe_wal_archiver(session)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a metrics poller must never kill the process
            logger.debug("wal archiver poll failed", exc_info=True)
        await asyncio.sleep(interval)
