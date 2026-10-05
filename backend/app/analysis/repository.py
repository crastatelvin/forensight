"""Job and result persistence (P4.B2) plus idempotent submission (P4.B3/T4.6).

Submission is idempotent at the database level: the UNIQUE constraint on
`analysis_jobs.idempotency_key` catches the duplicate, and the caller gets the
existing job back. A read-then-write check would let two concurrent workers both
see "no job yet" and both run the same analysis.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.models import AnalysisJob, AnalysisResult
from app.analysis.types import JobStatus


async def get_job(session: AsyncSession, job_id: uuid.UUID) -> AnalysisJob | None:
    return await session.get(AnalysisJob, job_id)


async def get_job_by_key(session: AsyncSession, idempotency_key: str) -> AnalysisJob | None:
    result = await session.execute(
        select(AnalysisJob).where(AnalysisJob.idempotency_key == idempotency_key)
    )
    return result.scalar_one_or_none()


async def get_result_for_key(session: AsyncSession, idempotency_key: str) -> AnalysisResult | None:
    result = await session.execute(
        select(AnalysisResult)
        .where(AnalysisResult.idempotency_key == idempotency_key)
        .order_by(AnalysisResult.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def submit_job(
    session: AsyncSession,
    *,
    case_id: uuid.UUID,
    evidence_id: uuid.UUID,
    evidence_sha256: str,
    module: str,
    module_version: str,
    params: dict[str, Any],
    requested_by: uuid.UUID,
    gpu_available: bool,
    unsupported_reason: str = "",
) -> tuple[AnalysisJob, bool]:
    """Create or reuse a job for this exact unit of work.

    Returns (job, created). `created` is False when an identical job already
    existed — the caller must then not enqueue it again.

    Raises IntegrityError-free: a losing racer catches the constraint violation
    here and falls back to reading the winner's row, so concurrent submissions of
    the same work collapse to a single job (T4.6).
    """
    from app.analysis.types import idempotency_key

    key = idempotency_key(
        evidence_sha256=evidence_sha256,
        evidence_id=str(evidence_id),
        module_name=module,
        params=params,
        model_version=module_version,
    )

    existing = await get_job_by_key(session, key)
    if existing is not None:
        return existing, False

    job = AnalysisJob(
        case_id=case_id,
        evidence_id=evidence_id,
        module=module,
        model_version=module_version,
        idempotency_key=key,
        status=(JobStatus.UNSUPPORTED if unsupported_reason else JobStatus.QUEUED).value,
        unsupported_reason=unsupported_reason,
        params=params,
        requested_by=requested_by,
    )
    session.add(job)
    try:
        await session.flush()
    except IntegrityError:
        # Another submission of the same key won the race. Roll back to a clean
        # state and adopt the winner's job so the caller still gets a handle.
        await session.rollback()
        winner = await get_job_by_key(session, key)
        if winner is None:
            raise
        return winner, False
    return job, True


async def mark_running(session: AsyncSession, job: AnalysisJob) -> None:
    job.status = JobStatus.RUNNING.value
    job.started_at = datetime.now(UTC)
    job.attempts += 1
    await session.flush()


async def mark_done(
    session: AsyncSession, job: AnalysisJob, payload: dict[str, Any]
) -> AnalysisResult:
    """Record the result and close the job. The result is append-only."""
    result = AnalysisResult(
        job_id=job.id,
        case_id=job.case_id,
        evidence_id=job.evidence_id,
        module=job.module,
        idempotency_key=job.idempotency_key,
        score=float(payload["score"]),
        threshold=float(payload["threshold"]),
        model_version=str(payload["model_version"]),
        tier=str(payload["tier"]),
        disclaimer=str(payload["disclaimer"]),
        payload=payload,
    )
    session.add(result)
    job.status = JobStatus.DONE.value
    job.finished_at = datetime.now(UTC)
    await session.flush()
    return result


async def mark_failed(session: AsyncSession, job: AnalysisJob, error: str) -> None:
    job.status = JobStatus.FAILED.value
    job.error = error[:4000]
    job.finished_at = datetime.now(UTC)
    await session.flush()


async def mark_unsupported(session: AsyncSession, job: AnalysisJob, reason: str) -> None:
    job.status = JobStatus.UNSUPPORTED.value
    job.unsupported_reason = reason
    job.finished_at = datetime.now(UTC)
    await session.flush()


async def list_jobs_for_case(
    session: AsyncSession, case_id: uuid.UUID, *, limit: int = 50, offset: int = 0
) -> list[AnalysisJob]:
    result = await session.execute(
        select(AnalysisJob)
        .where(AnalysisJob.case_id == case_id)
        .order_by(AnalysisJob.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars().all())


async def list_results_for_case(
    session: AsyncSession, case_id: uuid.UUID, *, limit: int = 50, offset: int = 0
) -> list[AnalysisResult]:
    result = await session.execute(
        select(AnalysisResult)
        .where(AnalysisResult.case_id == case_id)
        .order_by(AnalysisResult.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars().all())


async def list_results_for_evidence(
    session: AsyncSession, evidence_id: uuid.UUID, *, limit: int = 50, offset: int = 0
) -> list[AnalysisResult]:
    result = await session.execute(
        select(AnalysisResult)
        .where(AnalysisResult.evidence_id == evidence_id)
        .order_by(AnalysisResult.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars().all())
