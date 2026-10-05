"""Analysis API routes (Phase 4).

Submission is case-scoped like every other case route: an officer cannot analyse
another officer's evidence. Submission validates capability *before* queueing, so
an unsupported module never produces a queued job that can only fail.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis import registry
from app.analysis.queue import enqueue_analysis_job
from app.analysis.repository import (
    list_jobs_for_case,
    list_results_for_case,
    list_results_for_evidence,
    submit_job,
)
from app.analysis.schemas import (
    AnalysisJobRead,
    AnalysisRequest,
    AnalysisResultRead,
    ModuleRead,
)
from app.analysis.types import Tier
from app.auth.rbac import CurrentUser, get_current_user, require_roles
from app.auth.roles import MUTATING_ROLES
from app.config import Settings, get_settings
from app.db import get_session
from app.domain.access import visible_case
from app.domain.models import Case, Evidence
from app.domain.repository import append_audit

router = APIRouter(tags=["analysis"])

_MAX_PAGE = 200


async def _scoped_case(session: AsyncSession, case_id: uuid.UUID, user: CurrentUser) -> Case:
    case = await visible_case(session, case_id, user)
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    return case


@router.get("/modules", response_model=list[ModuleRead])
async def list_modules(
    _user: Annotated[CurrentUser, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> list[ModuleRead]:
    """Registered modules with their capability status on this host.

    Reports unavailable modules rather than hiding them, so a client can explain
    why a module cannot be run instead of showing an empty list (T4.4).
    """
    out: list[ModuleRead] = []
    for spec in registry.all_specs():
        gpu = getattr(settings, "gpu_available", False)
        out.append(
            ModuleRead(
                name=spec.name,
                version=spec.version,
                tier=spec.tier.value,
                threshold=spec.threshold,
                requires_gpu=spec.requires_gpu,
                accepts=spec.accepts,
                model_key=spec.model_key,
                available=not (spec.requires_gpu and not gpu),
                unavailable_reason=(
                    "" if not (spec.requires_gpu and not gpu) else "no GPU detected on this host"
                ),
            )
        )
    return out


@router.post("/cases/{case_id}/analyses", response_model=AnalysisJobRead, status_code=201)
async def submit_analysis(
    case_id: uuid.UUID,
    payload: AnalysisRequest,
    user: Annotated[CurrentUser, Depends(require_roles(*MUTATING_ROLES))],
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AnalysisJobRead:
    """Queue analysis of one evidence item with one module.

    Idempotent: resubmitting identical work returns the existing job (200-style
    reuse via `created`) instead of recomputing (P4.B3/T4.1).
    """
    case = await _scoped_case(session, case_id, user)
    evidence = await session.get(Evidence, payload.evidence_id)
    if evidence is None or evidence.case_id != case.id:
        raise HTTPException(status_code=404, detail="Evidence not found")

    if not registry.is_registered(payload.module):
        known = ", ".join(registry.registered_names()) or "none"
        raise HTTPException(
            status_code=422,
            detail=f"unknown module {payload.module!r}; registered: {known}",
        )
    module = registry.get(payload.module)

    unsupported = ""
    try:
        registry.check_capability(module, gpu_available=settings.gpu_available)
    except Exception as exc:  # UnsupportedCapability
        unsupported = str(exc)

    job, created = await submit_job(
        session,
        case_id=case.id,
        evidence_id=evidence.id,
        evidence_sha256=evidence.sha256,
        module=module.spec.name,
        module_version=module.spec.version,
        params=payload.params,
        requested_by=uuid.UUID(user.id),
        gpu_available=settings.gpu_available,
        unsupported_reason=unsupported,
    )
    await append_audit(
        session,
        actor_id=uuid.UUID(user.id),
        action="analysis.submit" if created else "analysis.reuse",
        target=str(job.id),
        payload={
            "module": job.module,
            "evidence_id": str(evidence.id),
            "cached": not created,
        },
    )
    await session.commit()
    # Only a newly created, runnable job gets queued: re-submitting identical
    # work must not queue a second execution (P4.B3).
    if created and not unsupported:
        enqueue_analysis_job(job.id, settings=settings)
    return AnalysisJobRead.model_validate(job)


@router.get("/cases/{case_id}/analyses", response_model=list[AnalysisJobRead])
async def list_analyses(
    case_id: uuid.UUID,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=_MAX_PAGE)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[AnalysisJobRead]:
    case = await _scoped_case(session, case_id, user)
    rows = await list_jobs_for_case(session, case.id, limit=limit, offset=offset)
    return [AnalysisJobRead.model_validate(r) for r in rows]


@router.get("/cases/{case_id}/results", response_model=list[AnalysisResultRead])
async def list_case_results(
    case_id: uuid.UUID,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=_MAX_PAGE)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[AnalysisResultRead]:
    case = await _scoped_case(session, case_id, user)
    rows = await list_results_for_case(session, case.id, limit=limit, offset=offset)
    return [AnalysisResultRead.model_validate(r) for r in rows]


@router.get("/evidence/{evidence_id}/results", response_model=list[AnalysisResultRead])
async def list_evidence_results(
    evidence_id: uuid.UUID,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=_MAX_PAGE)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[AnalysisResultRead]:
    evidence = await session.get(Evidence, evidence_id)
    if evidence is None:
        raise HTTPException(status_code=404, detail="Evidence not found")
    case = await visible_case(session, evidence.case_id, user)
    if case is None:
        raise HTTPException(status_code=404, detail="Evidence not found")
    rows = await list_results_for_evidence(session, evidence.id, limit=limit, offset=offset)
    return [AnalysisResultRead.model_validate(r) for r in rows]


@router.get("/analyses/jobs/{job_id}", response_model=AnalysisJobRead)
async def read_job(
    job_id: uuid.UUID,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AnalysisJobRead:
    from app.analysis.repository import get_job

    job = await get_job(session, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Analysis job not found")
    case = await visible_case(session, job.case_id, user)
    if case is None:
        raise HTTPException(status_code=404, detail="Analysis job not found")
    return AnalysisJobRead.model_validate(job)


@router.post("/analyses/jobs/{job_id}/run", response_model=AnalysisJobRead)
async def run_job_now(
    job_id: uuid.UUID,
    user: Annotated[CurrentUser, Depends(require_roles(*MUTATING_ROLES))],
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AnalysisJobRead:
    """Run a queued job inline (dev/tests only; workers normally do this).

    Deliberately explicit rather than automatic: a worker is the right place to
    do this work, and having an endpoint that runs ML inside the API process
    invites exactly the coupling DESIGN_PLAN forbids.
    """
    from app.analysis.executor import execute_job
    from app.analysis.repository import get_job

    job = await get_job(session, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Analysis job not found")
    case = await _scoped_case(session, job.case_id, user)

    await execute_job(
        session,
        job,
        settings=settings,
        gpu_available=settings.gpu_available,
    )
    await append_audit(
        session,
        actor_id=uuid.UUID(user.id),
        action="analysis.run",
        target=str(case.id),
        payload={"job_id": str(job.id), "module": job.module, "status": job.status},
    )
    await session.commit()
    return AnalysisJobRead.model_validate(job)


__all__ = ["router", "Tier"]
