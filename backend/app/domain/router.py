"""Case & evidence API routes (Phase 2/3).

Route-level RBAC (require_roles) gates *who may call* an endpoint; per-case
access control (app.domain.access) gates *which cases* a caller may see or
change (T3.1/T3.7). Download is strictly by evidence object-id — no path input
is ever accepted (T2.2).
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
from app.auth.repository import get_user_by_id
from app.auth.roles import Role
from app.config import Settings, get_settings
from app.db import get_session
from app.domain.access import assert_can_read, assert_can_write
from app.domain.antivirus import ClamAVScanner, InfectedFileError, NoopScanner, Scanner
from app.domain.exports import export_csv, export_json, export_zip
from app.domain.ingest import EvidenceValidationError
from app.domain.models import Case
from app.domain.repository import (
    append_audit,
    custody_for_evidence,
    get_case,
    get_evidence,
    list_case_evidence,
    search_cases,
)
from app.domain.schemas import (
    CaseAssign,
    CaseCreate,
    CaseRead,
    CaseTransition,
    CustodyEntry,
    EntityCreate,
    EntityRead,
    EvidenceRead,
    FindingCreate,
    FindingRead,
    TimelineEventRead,
)
from app.domain.service import IngestService
from app.domain.state_machine import IllegalTransitionError, assert_transition
from app.domain.storage import ObjectStore
from app.domain.workflow_repo import (
    create_entity,
    create_finding,
    list_entities,
    list_findings,
    list_timeline,
)

router = APIRouter(prefix="/cases", tags=["cases"])

# Module-level notifier, wired by the app factory (RedisNotifier in prod,
# InMemoryNotifier in tests). None means notifications are a no-op.
_NOTIFIER: object | None = None

_MUTATORS = (Role.ADMIN, Role.OFFICER, Role.EXPERT)


def _scanner(settings: Settings) -> Scanner:
    host = getattr(settings, "clamav_host", "")
    return ClamAVScanner(host) if host else NoopScanner()


def _ingest_service(settings: Settings) -> IngestService:
    return IngestService(ObjectStore(settings), _scanner(settings))


async def _load_case_for_read(
    session: AsyncSession, case_id: uuid.UUID, user: CurrentUser
) -> Case:
    """Fetch a case and assert the caller may read it (404 on miss/no-access)."""
    case = await get_case(session, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    assert_can_read(case, user)
    return case


async def _load_case_for_write(
    session: AsyncSession, case_id: uuid.UUID, user: CurrentUser
) -> Case:
    """Fetch a case and assert the caller may modify it."""
    case = await get_case(session, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    assert_can_write(case, user)
    return case


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
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CaseRead:
    case = await _load_case_for_read(session, case_id, user)
    return CaseRead.model_validate(case)


@router.post("/{case_id}/transition", response_model=CaseRead)
async def transition_case(
    case_id: uuid.UUID,
    payload: CaseTransition,
    user: Annotated[CurrentUser, Depends(require_roles(*_MUTATORS))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CaseRead:
    case = await _load_case_for_write(session, case_id, user)
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
    await _load_case_for_write(session, case_id, user)

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
    evidence = await get_evidence(session, evidence_id)
    if evidence is None:
        raise HTTPException(status_code=404, detail="Evidence not found")
    # T3.7: caller must be able to read the owning case.
    await _load_case_for_read(session, evidence.case_id, user)
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
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[CustodyEntry]:
    # T2.8: full chain-of-custody timeline with actor, time, action.
    evidence = await get_evidence(session, evidence_id)
    if evidence is None:
        raise HTTPException(status_code=404, detail="Evidence not found")
    await _load_case_for_read(session, evidence.case_id, user)
    entries = await custody_for_evidence(session, evidence_id)
    return [CustodyEntry.model_validate(e) for e in entries]


# --- Assignment (T3.2) ---

@router.post("/{case_id}/assign", response_model=CaseRead)
async def assign_case(
    case_id: uuid.UUID,
    payload: CaseAssign,
    user: Annotated[CurrentUser, Depends(require_roles(Role.ADMIN, Role.OFFICER))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CaseRead:
    case = await _load_case_for_write(session, case_id, user)
    # Validate the assignee actually exists (no dangling assignments).
    assignee = await get_user_by_id(session, payload.assigned_to)
    if assignee is None:
        raise HTTPException(status_code=422, detail="Assignee user does not exist")
    case.assigned_to = payload.assigned_to
    await append_audit(
        session, actor_id=uuid.UUID(user.id), action="case.assign",
        target=str(case.id), payload={"assigned_to": str(payload.assigned_to)},
    )
    await session.commit()
    await _notify(
        case_id=case.id, event="case.assigned",
        payload={"assigned_to": str(payload.assigned_to), "by": user.id},
    )
    return CaseRead.model_validate(case)


async def _notify(*, case_id: uuid.UUID, event: str, payload: dict[str, object]) -> None:
    # Resolved lazily from the app to avoid import cycles; patched in tests.
    from app.domain import router as _self

    notifier = getattr(_self, "_NOTIFIER", None)
    if notifier is not None:
        await notifier.publish(event, {"case_id": str(case_id), **payload})


# --- Evidence listing (T3.1) ---

@router.get("/{case_id}/evidence", response_model=list[EvidenceRead])
async def list_evidence(
    case_id: uuid.UUID,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[EvidenceRead]:
    await _load_case_for_read(session, case_id, user)
    items = await list_case_evidence(session, case_id)
    return [EvidenceRead.model_validate(e) for e in items]


# --- Findings CRUD (draft) ---

@router.post("/{case_id}/findings", response_model=FindingRead, status_code=201)
async def add_finding(
    case_id: uuid.UUID,
    payload: FindingCreate,
    user: Annotated[CurrentUser, Depends(require_roles(*_MUTATORS))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> FindingRead:
    await _load_case_for_write(session, case_id, user)
    finding = await create_finding(
        session, case_id=case_id, title=payload.title, body=payload.body,
        created_by=uuid.UUID(user.id),
    )
    await append_audit(
        session, actor_id=uuid.UUID(user.id), action="finding.create",
        target=str(finding.id), payload={"case_id": str(case_id)},
    )
    await session.commit()
    return FindingRead.model_validate(finding)


@router.get("/{case_id}/findings", response_model=list[FindingRead])
async def get_findings(
    case_id: uuid.UUID,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[FindingRead]:
    await _load_case_for_read(session, case_id, user)
    return [FindingRead.model_validate(f) for f in await list_findings(session, case_id)]


# --- Entities ---

@router.post("/{case_id}/entities", response_model=EntityRead, status_code=201)
async def add_entity(
    case_id: uuid.UUID,
    payload: EntityCreate,
    user: Annotated[CurrentUser, Depends(require_roles(*_MUTATORS))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> EntityRead:
    await _load_case_for_write(session, case_id, user)
    entity = await create_entity(
        session, case_id=case_id, kind=payload.kind, value=payload.value,
        attributes=payload.attributes,
    )
    await append_audit(
        session, actor_id=uuid.UUID(user.id), action="entity.create",
        target=str(entity.id), payload={"case_id": str(case_id), "kind": entity.kind},
    )
    await session.commit()
    return EntityRead.model_validate(entity)


@router.get("/{case_id}/entities", response_model=list[EntityRead])
async def get_entities(
    case_id: uuid.UUID,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[EntityRead]:
    await _load_case_for_read(session, case_id, user)
    return [EntityRead.model_validate(e) for e in await list_entities(session, case_id)]


@router.get("/{case_id}/timeline", response_model=list[TimelineEventRead])
async def get_timeline(
    case_id: uuid.UUID,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[TimelineEventRead]:
    await _load_case_for_read(session, case_id, user)
    return [
        TimelineEventRead.model_validate(t) for t in await list_timeline(session, case_id)
    ]


# --- Search (T3.3/T3.7) ---

@router.get("/search/query", response_model=list[CaseRead])
async def search(
    q: str,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[CaseRead]:
    # Admin and reviewer search organisation-wide; officer/expert are scoped to
    # cases they own or are assigned to, so search never leaks out-of-scope cases.
    all_access = user.role in (Role.ADMIN, Role.REVIEWER)
    results = await search_cases(
        session, q, viewer_id=uuid.UUID(user.id), all_access=all_access
    )
    return [CaseRead.model_validate(c) for c in results]


# --- Exports (T3.4) ---

@router.get("/{case_id}/export")
async def export_case(
    case_id: uuid.UUID,
    fmt: str,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    case = await _load_case_for_read(session, case_id, user)
    evidence = await list_case_evidence(session, case_id)

    if fmt == "json":
        return Response(
            content=export_json(case, evidence),
            media_type="application/json",
            headers={
                "Content-Disposition": f'attachment; filename="{case.number}.json"'
            },
        )
    if fmt == "csv":
        return Response(
            content=export_csv(evidence),
            media_type="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="{case.number}.csv"'
            },
        )
    if fmt == "zip":
        return Response(
            content=export_zip(case, evidence),
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{case.number}.zip"'
            },
        )
    raise HTTPException(status_code=422, detail="fmt must be one of: json, csv, zip")
