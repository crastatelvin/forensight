"""Enqueue analysis jobs (P4.B1).

Isolated in one module so the broker choice stays swappable: nothing else in the
codebase imports dramatiq. When no broker is configured (dev, tests, CI), the
queue is a no-op that reports `False`, and jobs are executed via
`POST /analyses/jobs/{id}/run` or directly through `execute_job`.
"""

from __future__ import annotations

import logging
import uuid

from app import tracing
from app.config import Settings

logger = logging.getLogger("forensight.queue")


def is_configured(settings: Settings) -> bool:
    return bool(settings.queue_url)


def enqueue_analysis_job(job_id: uuid.UUID, *, settings: Settings) -> bool:
    """Queue a job for a worker. Returns False when no broker is available.

    Never raises: a broker outage must not fail the API request that already
    created and committed the job. The job stays `queued` and can be picked up by
    an operator via the inline run endpoint.

    **The current `traceparent` rides along in the message options** (P10.B1). Without
    it the worker has no idea which trace it is continuing, so it starts a fresh root
    and the API's span and the worker's span sit in two unrelated traces that, in a
    trace viewer, look like two separate operations. The job id is the only thing that
    crosses the process boundary today, and a trace that stops at the boundary looks
    continuous while measuring nothing.
    """
    if not is_configured(settings):
        logger.info(
            "no queue broker configured; job %s left queued for inline execution",
            job_id,
        )
        return False
    try:
        from workers.dramatiq_worker import run_analysis_job

        traceparent = tracing.current_traceparent()
        if traceparent:
            run_analysis_job.send_with_options(
                args=(str(job_id),), options={"traceparent": traceparent}
            )
        else:
            run_analysis_job.send(str(job_id))
        return True
    except Exception as exc:  # noqa: BLE001 - broker outage must not 500 the API
        logger.warning("failed to enqueue analysis job %s: %s", job_id, exc)
        return False
