"""Case & evidence API routes (Phase 2).

Download is strictly by evidence object-id + case authz — no path input is
ever accepted (T2.2). Mutations are limited to non-reviewer roles.
"""
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Response,
    UploadFile,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.rbac import CurrentUser, get_current_user, require_roles
from app.auth.roles import Role
from app.config import Settings, get_settings
from app.db import get_session
from app.domain.antivirus import ClamAVScanner, InfectedFileError, NoopScanner, Scanner
from app.domain.ingest import EvidenceValidationError
from app.domain.models import Case
from app.domain.repository import (
    append_audit,
    custody_for_evidence,
    get_case,
)
from app.domain.schemas import (
    CaseCreate,
    CaseRead,
    CaseTransition,
    CustodyEntry,
    EvidenceRead,
)
from app.domain.service import IngestService
from app.domain.state_machine import IllegalTransitionError, assert_transition
from app.domain.storage import ObjectStore

router = APIRouter(prefix="/cases", tags=["cases"])

_MUTATORS = (Role.ADMIN, Role.OFFICER, Role.EXPERT)


def _scanner(settings: Settings) -> Scanner:
    host = getattr(settings, "clamav_host", "")
    return ClamAVScanner(host) if host else NoopScanner()


def _ingest_service(settings: Settings) -> IngestService:
    return IngestService(ObjectStore(settings), _scanner(settings))


@router.post("", response_model=CaseRead, status_code=201)
async def create_case(
    payload: CaseCreate,
    user: Annotated[CurrentUser, Depends(require_roles(*_MUTATORS))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CaseRead:
    case = Case(
        number=payload.number,
        title=payload.title,
        description=payload.description,
        owner_id=uuid.UUID(user.id),
    )
    session.add(case)
    await session.flush()
    await append_audit(
        session, actor_id=uuid.UUID(user.id), action="case.create",
        target=str(case.id), payload={"number": case.number},
    )
    await session.commit()
    return CaseRead.model_validate(case)


@router.get("/{case_id}", response_model=CaseRead)
async def read_case(
    case_id: uuid.UUID,
    _user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CaseRead:
    case = await get_case(session, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    return CaseRead.model_validate(case)


@router.post("/{case_id}/transition", response_model=CaseRead)
async def transition_case(
    case_id: uuid.UUID,
    payload: CaseTransition,
    user: Annotated[CurrentUser, Depends(require_roles(*_MUTATORS))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CaseRead:
    case = await get_case(session, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    try:
        assert_transition(case.status, payload.target)
    except IllegalTransitionError as exc:
        # T2.7: illegal transition -> 409 + audit.
        await append_audit(
            session, actor_id=uuid.UUID(user.id), action="case.transition.rejected",
            target=str(case.id), payload={"from": case.status, "to": payload.target},
        )
        await session.commit()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    case.status = payload.target
    await append_audit(
        session, actor_id=uuid.UUID(user.id), action="case.transition",
        target=str(case.id), payload={"to": payload.target},
    )
    await session.commit()
    return CaseRead.model_validate(case)


@router.post("/{case_id}/evidence", response_model=EvidenceRead, status_code=201)
async def upload_evidence(
    case_id: uuid.UUID,
    user: Annotated[CurrentUser, Depends(require_roles(*_MUTATORS))],
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    file: Annotated[UploadFile, File()],
) -> EvidenceRead:
    _ = Form  # multipart form dependency marker (kept for future fields)
    case = await get_case(session, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")

    data = await file.read()
    service = _ingest_service(settings)
    try:
        evidence = await service.ingest(
            session,
            case_id=case_id,
            actor_id=uuid.UUID(user.id),
            filename=file.filename or "unnamed",
            data=data,
        )
    except EvidenceValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except InfectedFileError as exc:
        raise HTTPException(
            status_code=422, detail=f"Malware detected: {exc.signature}"
        ) from exc
    await session.commit()
    return EvidenceRead.model_validate(evidence)


@router.get("/evidence/{evidence_id}/download")
async def download_evidence(
    evidence_id: uuid.UUID,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Response:
    # T2.2: object-id only; no path input is ever accepted.
    service = _ingest_service(settings)
    try:
        evidence, data = await service.download(
            session, evidence_id=evidence_id, actor_id=uuid.UUID(user.id)
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Evidence not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await session.commit()
    return Response(
        content=data,
        media_type=evidence.content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{evidence.filename}"'
        },
    )


@router.get("/evidence/{evidence_id}/custody", response_model=list[CustodyEntry])
async def evidence_custody(
    evidence_id: uuid.UUID,
    _user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[CustodyEntry]:
    # T2.8: full chain-of-custody timeline with actor, time, action.
    entries = await custody_for_evidence(session, evidence_id)
    return [CustodyEntry.model_validate(e) for e in entries]
