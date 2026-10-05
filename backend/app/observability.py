"""Logging + Prometheus metrics (P10.B1 groundwork).

`infra/observability/prometheus.yml` scrapes `/metrics` on this service, so the
endpoint has to exist or Prometheus records the target as permanently down.
Metrics are deliberately few: request rate, error rate and latency per route,
which is what the SLOs in the plan actually need.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response as StarletteResponse

from app.config import Settings

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
