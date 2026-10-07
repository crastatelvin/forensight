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

from app import drain
from app.analysis import lab_only, registry
from app.analysis.overlay import router as overlay_router
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
from app.analysis.types import TIER_MEANING, Tier
from app.auth.rbac import CurrentUser, get_current_user, require_roles
from app.auth.roles import MUTATING_ROLES
from app.config import Settings, get_settings
from app.db import get_session
from app.domain.access import scoped_case, visible_case
from app.domain.models import Evidence
from app.domain.repository import append_audit
from app.drain import is_draining

router = APIRouter(tags=["analysis"])

_MAX_PAGE = 200


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


@router.get("/tiers")
async def list_tiers(
    _user: Annotated[CurrentUser, Depends(get_current_user)],
) -> list[dict[str, str]]:
    """What each tier means, for the interface to render.

    The interface asks rather than keeping its own copy. It had one, and the copy
    disagreed with this code on what Tier B and Tier C mean — a disagreement about
    how far a number can be pushed, which is the one thing a tier exists to fix.
    """
    return [{"tier": tier, **meaning} for tier, meaning in TIER_MEANING.items()]


@router.get("/lab-techniques")
async def list_lab_techniques(
    _user: Annotated[CurrentUser, Depends(get_current_user)],
) -> list[dict[str, str]]:
    """Tier-C techniques: named, explained, and not scoreable.

    Served separately from `/modules` because a catalog entry with no scoring path
    is not an analyzer. Listing these alongside runnable modules would imply they
    can be queued, which is the one thing they must not imply.
    """
    return lab_only.catalog()


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
    if is_draining():
        # 503, not 409 or 400: this is "not this instance's problem, try another",
        # which is what a client, a load balancer and a retry loop all understand.
        raise HTTPException(status_code=503, detail=drain.refusal("new analyses"))

    case = await scoped_case(session, case_id, user)
    evidence = await session.get(Evidence, payload.evidence_id)
    if evidence is None or evidence.case_id != case.id:
        raise HTTPException(status_code=404, detail="Evidence not found")

    # Tier C is refused here, before any row exists. Not queued and marked
    # `unsupported`: there is no module to provision, so a job row and a worker
    # message would both be records of something this platform will never attempt.
    # A test asserts that no job is created, because a Tier-C attempt that left a
    # row behind would be indistinguishable from a real analysis in the case view.
    if lab_only.is_lab_only(payload.module):
        await append_audit(
            session,
            actor_id=uuid.UUID(user.id),
            action="analysis.refused",
            target=str(payload.evidence_id),
            payload={"module": payload.module, "reason": lab_only.REFUSAL_PREFIX},
        )
        await session.commit()
        raise HTTPException(status_code=422, detail=lab_only.refusal(payload.module))

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
    case = await scoped_case(session, case_id, user)
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
    case = await scoped_case(session, case_id, user)
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
    case = await scoped_case(session, job.case_id, user)

    from app.analysis.models_registry import ModelStore

    await execute_job(
        session,
        job,
        settings=settings,
        gpu_available=settings.gpu_available,
        # A ModelStore, so the inline path can load pinned weights. Same reason as
        # the worker: without it every model-backed module failed here with "needs a
        # verified model but no store was provided".
        model_store=ModelStore(settings),
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


__all__ = ["router", "overlay_router", "Tier"]
