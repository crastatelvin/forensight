"""Case & evidence API routes (Phase 2/3).

Two layers of authorization apply to every route here:
- RBAC (`app.auth.rbac`) — which roles may call a mutating route at all.
- Object scope (`app.domain.access`) — which specific cases that user may see.

Download is strictly by evidence object-id + case scope — no path input is ever
accepted (T2.2, P2.B5). Reviewer is read-only and never appears in a mutating
role set.
"""

from __future__ import annotations

import contextlib
import uuid
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    Response,
    UploadFile,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import User
from app.auth.rbac import CurrentUser, get_current_user, require_roles
from app.auth.roles import MUTATING_ROLES, Role
from app.auth.service import mutation_rate_limit
from app.config import Settings, get_settings
from app.db import get_session
from app.domain.access import visible_case
from app.domain.antivirus import ClamAVScanner, InfectedFileError, NoopScanner, Scanner
from app.domain.exports import export_csv, export_json, export_zip
from app.domain.ingest import EvidenceValidationError
from app.domain.models import AuditLog, Case, Evidence
from app.domain.notifications import Notifier
from app.domain.repository import (
    append_audit,
    custody_for_evidence,
    get_evidence,
    list_audit,
    list_case_evidence,
    list_cases,
    search_cases,
    verify_audit_chain,
)
from app.domain.schemas import (
    AuditChainStatus,
    AuditEntryRead,
    CaseAssign,
    CaseCreate,
    CaseRead,
    CaseTransition,
    CaseUpdate,
    CustodyEntry,
    EntityCreate,
    EntityRead,
    EvidenceRead,
    FindingCreate,
    FindingRead,
    FindingUpdate,
    TimelineEventCreate,
    TimelineEventRead,
)
from app.domain.service import IngestService
from app.domain.state_machine import IllegalTransitionError, assert_transition
from app.domain.storage import ObjectStore
from app.domain.workflow_repo import (
    create_entity,
    create_finding,
    create_timeline_event,
    delete_entity,
    delete_finding,
    get_entity,
    get_finding,
    list_entities,
    list_findings,
    list_timeline,
    update_finding,
)

# Rate limiting is applied once, centrally, to every route on this router
# (P1.B4) rather than copy-pasted per endpoint.
router = APIRouter(
    prefix="/cases",
    tags=["cases"],
    dependencies=[Depends(mutation_rate_limit)],
)

# Notifier is wired by the app factory (RedisNotifier in prod, InMemoryNotifier
# in tests). None means notifications are a no-op.
_NOTIFIER: Notifier | None = None

# Bounds on every list endpoint so a case with 10k evidence items cannot be
# turned into a single unbounded response.
_MAX_PAGE = 200


def _scanner(settings: Settings) -> Scanner:
    host = getattr(settings, "clamav_host", "")
    return ClamAVScanner(host) if host else NoopScanner()


def _ingest_service(settings: Settings) -> IngestService:
    return IngestService(ObjectStore(settings), _scanner(settings))


def _limit(limit: int) -> int:
    return max(1, min(limit, _MAX_PAGE))


async def _scoped_case(session: AsyncSession, case_id: uuid.UUID, user: CurrentUser) -> Case:
    """Load a case the user is allowed to see, or 404.

    A case that exists but belongs to someone else is reported as 404, not 403:
    a 403 would confirm the id is real to a user with no right to know that.
    """
    case = await visible_case(session, case_id, user)
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    return case


async def _scoped_evidence(
    session: AsyncSession, evidence_id: uuid.UUID, user: CurrentUser
) -> Evidence:
    """Load evidence only when its parent case is visible to the user."""
    evidence = await get_evidence(session, evidence_id)
    if evidence is None:
        raise HTTPException(status_code=404, detail="Evidence not found")
    case = await visible_case(session, evidence.case_id, user)
    if case is None:
        raise HTTPException(status_code=404, detail="Evidence not found")
    return evidence


async def _notify(*, case_id: uuid.UUID, event: str, payload: dict[str, Any]) -> None:
    """Publish a notification, never failing the request that caused it.

    A dead Redis must not turn "assign this case" into a 500 — assignment is
    already committed and audited; the notification is best-effort.
    """
    notifier = _NOTIFIER
    if notifier is None:
        return
    with contextlib.suppress(Exception):
        # Notifications are advisory: a dead Redis must never turn an already
        # committed, already-audited assignment into a 500.
        await notifier.publish(event, {"case_id": str(case_id), **payload})


# --- Cases ---


@router.post("", response_model=CaseRead, status_code=201)
async def create_case(
    payload: CaseCreate,
    user: Annotated[CurrentUser, Depends(require_roles(*MUTATING_ROLES))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CaseRead:
    case = Case(
        number=payload.number,
        title=payload.title,
        description=payload.description,
        priority=payload.priority,
        owner_id=uuid.UUID(user.id),
    )
    session.add(case)
    await session.flush()
    await append_audit(
        session,
        actor_id=uuid.UUID(user.id),
        action="case.create",
        target=str(case.id),
        payload={"number": case.number},
    )
    await session.commit()
    return CaseRead.model_validate(case)


@router.get("/audit", response_model=list[AuditEntryRead])
async def read_audit(
    _user: Annotated[CurrentUser, Depends(require_roles(Role.ADMIN))],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=_MAX_PAGE)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[AuditEntryRead]:
    """Newest-first audit page. Admin-only: the log spans every case.

    Declared before `/{case_id}` so the literal path wins the route match —
    otherwise "audit" is parsed as a UUID and every call 422s.
    """
    rows = await list_audit(session, limit=limit, offset=offset)
    return [AuditEntryRead.model_validate(r) for r in rows]


@router.get("/audit/verify", response_model=AuditChainStatus)
async def verify_audit(
    _user: Annotated[CurrentUser, Depends(require_roles(Role.ADMIN))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AuditChainStatus:
    """Recompute the entire hash chain and report whether it still holds.

    Exposed because an unverifiable chain is not evidence of anything — the
    verifier has to be reachable, not just correct in a unit test.
    """
    total = (await session.execute(select(func.count()).select_from(AuditLog))).scalar_one()
    return AuditChainStatus(valid=await verify_audit_chain(session), entries=int(total))


@router.get("", response_model=list[CaseRead])
async def list_all_cases(
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=_MAX_PAGE)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[CaseRead]:
    """Cases visible to the caller, newest first (T3.1)."""
    cases = await list_cases(session, limit=limit, offset=offset, user=user)
    return [CaseRead.model_validate(c) for c in cases]


@router.get("/{case_id}", response_model=CaseRead)
async def read_case(  # noqa: D401 - must stay after the literal paths above
    case_id: uuid.UUID,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CaseRead:
    case = await _scoped_case(session, case_id, user)
    return CaseRead.model_validate(case)


@router.patch("/{case_id}", response_model=CaseRead)
async def update_case(
    case_id: uuid.UUID,
    payload: CaseUpdate,
    user: Annotated[CurrentUser, Depends(require_roles(*MUTATING_ROLES))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CaseRead:
    case = await _scoped_case(session, case_id, user)
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        return CaseRead.model_validate(case)
    for field, value in changes.items():
        setattr(case, field, value)
    await append_audit(
        session,
        actor_id=uuid.UUID(user.id),
        action="case.update",
        target=str(case.id),
        payload={"fields": sorted(changes)},
    )
    await session.commit()
    return CaseRead.model_validate(case)


@router.post("/{case_id}/transition", response_model=CaseRead)
async def transition_case(
    case_id: uuid.UUID,
    payload: CaseTransition,
    user: Annotated[CurrentUser, Depends(require_roles(*MUTATING_ROLES))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CaseRead:
    case = await _scoped_case(session, case_id, user)
    try:
        assert_transition(case.status, payload.target)
    except IllegalTransitionError as exc:
        # T2.7: illegal transition -> 409 + audit.
        await append_audit(
            session,
            actor_id=uuid.UUID(user.id),
            action="case.transition.rejected",
            target=str(case.id),
            payload={"from": case.status, "to": payload.target},
        )
        await session.commit()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    case.status = payload.target
    await append_audit(
        session,
        actor_id=uuid.UUID(user.id),
        action="case.transition",
        target=str(case.id),
        payload={"to": payload.target},
    )
    await session.commit()
    return CaseRead.model_validate(case)


# --- Evidence ---


@router.post("/{case_id}/evidence", response_model=EvidenceRead, status_code=201)
async def upload_evidence(
    case_id: uuid.UUID,
    user: Annotated[CurrentUser, Depends(require_roles(*MUTATING_ROLES))],
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    file: Annotated[UploadFile, File()],
) -> EvidenceRead:
    case = await _scoped_case(session, case_id, user)

    service = _ingest_service(settings)
    try:
        # Read inside the try: the streaming size cap raises the same
        # validation error as every other ingest rejection, and a 500 for an
        # oversize upload would both leak and mislead.
        data = await _read_capped(file)
        evidence = await service.ingest(
            session,
            case_id=case.id,
            actor_id=uuid.UUID(user.id),
            filename=file.filename or "unnamed",
            data=data,
        )
    except EvidenceValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except InfectedFileError as exc:
        raise HTTPException(status_code=422, detail=f"Malware detected: {exc.signature}") from exc
    await session.commit()
    return EvidenceRead.model_validate(evidence)


async def _read_capped(file: UploadFile) -> bytes:
    """Read an upload, aborting as soon as it exceeds the ingest limit.

    Reading in chunks bounds peak memory to the cap (plus one chunk) instead of
    the attacker's chosen size, and lets a >2 GiB body be rejected without ever
    being materialised.
    """
    from app.domain.ingest import MAX_EVIDENCE_BYTES

    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_EVIDENCE_BYTES:
            raise EvidenceValidationError(
                f"File exceeds maximum size of {MAX_EVIDENCE_BYTES} bytes"
            )
        chunks.append(chunk)
    return b"".join(chunks)


@router.get("/{case_id}/evidence", response_model=list[EvidenceRead])
async def list_evidence(
    case_id: uuid.UUID,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=_MAX_PAGE)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[EvidenceRead]:
    case = await _scoped_case(session, case_id, user)
    items = await list_case_evidence(session, case.id, limit=_limit(limit), offset=offset)
    return [EvidenceRead.model_validate(e) for e in items]


@router.get("/evidence/{evidence_id}/download")
async def download_evidence(
    evidence_id: uuid.UUID,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Response:
    # T2.2: object-id only; no path input is ever accepted.
    evidence = await _scoped_evidence(session, evidence_id, user)
    service = _ingest_service(settings)
    try:
        evidence, data = await service.download(
            session, evidence_id=evidence.id, actor_id=uuid.UUID(user.id)
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Evidence not found") from exc
    except ValueError as exc:
        # The integrity-failure audit row was flushed but not committed; without
        # this commit the one event that most needs to be durable is rolled back.
        await session.commit()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await session.commit()
    return Response(
        content=data,
        media_type=evidence.content_type,
        headers={"Content-Disposition": f'attachment; filename="{evidence.filename}"'},
    )


@router.get("/evidence/{evidence_id}/custody", response_model=list[CustodyEntry])
async def evidence_custody(
    evidence_id: uuid.UUID,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=_MAX_PAGE)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[CustodyEntry]:
    # T2.8: full chain-of-custody timeline with actor, time, action.
    evidence = await _scoped_evidence(session, evidence_id, user)
    entries = await custody_for_evidence(session, evidence.id)
    return [CustodyEntry.model_validate(e) for e in entries[offset : offset + _limit(limit)]]


# --- Assignment (T3.2) ---


@router.post("/{case_id}/assign", response_model=CaseRead)
async def assign_case(
    case_id: uuid.UUID,
    payload: CaseAssign,
    user: Annotated[CurrentUser, Depends(require_roles(Role.ADMIN, Role.OFFICER))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CaseRead:
    case = await _scoped_case(session, case_id, user)
    target_user = await session.get(User, payload.assigned_to)
    if target_user is None:
        raise HTTPException(status_code=422, detail="Assignee does not exist")
    case.assigned_to = payload.assigned_to
    await append_audit(
        session,
        actor_id=uuid.UUID(user.id),
        action="case.assign",
        target=str(case.id),
        payload={"assigned_to": str(payload.assigned_to)},
    )
    await session.commit()
    await _notify(
        case_id=case.id,
        event="case.assigned",
        payload={"assigned_to": str(payload.assigned_to), "by": user.id},
    )
    return CaseRead.model_validate(case)


# --- Findings ---


@router.post("/{case_id}/findings", response_model=FindingRead, status_code=201)
async def add_finding(
    case_id: uuid.UUID,
    payload: FindingCreate,
    user: Annotated[CurrentUser, Depends(require_roles(*MUTATING_ROLES))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> FindingRead:
    case = await _scoped_case(session, case_id, user)
    finding = await create_finding(
        session,
        case_id=case.id,
        title=payload.title,
        body=payload.body,
        created_by=uuid.UUID(user.id),
    )
    await append_audit(
        session,
        actor_id=uuid.UUID(user.id),
        action="finding.create",
        target=str(finding.id),
        payload={"case_id": str(case.id)},
    )
    await session.commit()
    return FindingRead.model_validate(finding)


@router.get("/{case_id}/findings", response_model=list[FindingRead])
async def get_findings(
    case_id: uuid.UUID,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=_MAX_PAGE)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[FindingRead]:
    case = await _scoped_case(session, case_id, user)
    rows = await list_findings(session, case.id, limit=_limit(limit), offset=offset)
    return [FindingRead.model_validate(f) for f in rows]


@router.patch("/{case_id}/findings/{finding_id}", response_model=FindingRead)
async def edit_finding(
    case_id: uuid.UUID,
    finding_id: uuid.UUID,
    payload: FindingUpdate,
    user: Annotated[CurrentUser, Depends(require_roles(*MUTATING_ROLES))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> FindingRead:
    case = await _scoped_case(session, case_id, user)
    finding = await get_finding(session, finding_id)
    if finding is None or finding.case_id != case.id:
        raise HTTPException(status_code=404, detail="Finding not found")
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        return FindingRead.model_validate(finding)
    await update_finding(session, finding, **changes)
    await append_audit(
        session,
        actor_id=uuid.UUID(user.id),
        action="finding.update",
        target=str(finding.id),
        payload={"fields": sorted(changes)},
    )
    await session.commit()
    return FindingRead.model_validate(finding)


@router.delete("/{case_id}/findings/{finding_id}", status_code=204)
async def remove_finding(
    case_id: uuid.UUID,
    finding_id: uuid.UUID,
    user: Annotated[CurrentUser, Depends(require_roles(*MUTATING_ROLES))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    case = await _scoped_case(session, case_id, user)
    finding = await get_finding(session, finding_id)
    if finding is None or finding.case_id != case.id:
        raise HTTPException(status_code=404, detail="Finding not found")
    await delete_finding(session, finding)
    await append_audit(
        session,
        actor_id=uuid.UUID(user.id),
        action="finding.delete",
        target=str(finding_id),
        payload={"case_id": str(case.id)},
    )
    await session.commit()
    return Response(status_code=204)


# --- Entities ---


@router.post("/{case_id}/entities", response_model=EntityRead, status_code=201)
async def add_entity(
    case_id: uuid.UUID,
    payload: EntityCreate,
    user: Annotated[CurrentUser, Depends(require_roles(*MUTATING_ROLES))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> EntityRead:
    case = await _scoped_case(session, case_id, user)
    entity = await create_entity(
        session,
        case_id=case.id,
        kind=payload.kind,
        value=payload.value,
        attributes=payload.attributes,
    )
    await append_audit(
        session,
        actor_id=uuid.UUID(user.id),
        action="entity.create",
        target=str(entity.id),
        payload={"case_id": str(case.id), "kind": entity.kind},
    )
    await session.commit()
    return EntityRead.model_validate(entity)


@router.get("/{case_id}/entities", response_model=list[EntityRead])
async def get_entities(
    case_id: uuid.UUID,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=_MAX_PAGE)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[EntityRead]:
    case = await _scoped_case(session, case_id, user)
    rows = await list_entities(session, case.id, limit=_limit(limit), offset=offset)
    return [EntityRead.model_validate(e) for e in rows]


@router.delete("/{case_id}/entities/{entity_id}", status_code=204)
async def remove_entity(
    case_id: uuid.UUID,
    entity_id: uuid.UUID,
    user: Annotated[CurrentUser, Depends(require_roles(*MUTATING_ROLES))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    case = await _scoped_case(session, case_id, user)
    entity = await get_entity(session, entity_id)
    if entity is None or entity.case_id != case.id:
        raise HTTPException(status_code=404, detail="Entity not found")
    await delete_entity(session, entity)
    await append_audit(
        session,
        actor_id=uuid.UUID(user.id),
        action="entity.delete",
        target=str(entity_id),
        payload={"case_id": str(case.id)},
    )
    await session.commit()
    return Response(status_code=204)


# --- Timeline ---


@router.post("/{case_id}/timeline", response_model=TimelineEventRead, status_code=201)
async def add_timeline_event(
    case_id: uuid.UUID,
    payload: TimelineEventCreate,
    user: Annotated[CurrentUser, Depends(require_roles(*MUTATING_ROLES))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TimelineEventRead:
    case = await _scoped_case(session, case_id, user)
    event = await create_timeline_event(
        session,
        case_id=case.id,
        label=payload.label,
        occurred_at=payload.occurred_at,
        source_ref=payload.source_ref,
        uncertain=payload.uncertain,
    )
    await append_audit(
        session,
        actor_id=uuid.UUID(user.id),
        action="timeline.create",
        target=str(event.id),
        payload={"case_id": str(case.id), "label": event.label},
    )
    await session.commit()
    return TimelineEventRead.model_validate(event)


@router.get("/{case_id}/timeline", response_model=list[TimelineEventRead])
async def get_timeline(
    case_id: uuid.UUID,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=_MAX_PAGE)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[TimelineEventRead]:
    case = await _scoped_case(session, case_id, user)
    rows = await list_timeline(session, case.id, limit=_limit(limit), offset=offset)
    return [TimelineEventRead.model_validate(t) for t in rows]


# --- Search (T3.3) ---


@router.get("/search/query", response_model=list[CaseRead])
async def search(
    q: Annotated[str, Query(min_length=1, max_length=256)],
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=_MAX_PAGE)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[CaseRead]:
    results = await search_cases(session, q, limit=limit, user=user)
    return [CaseRead.model_validate(c) for c in results[offset : offset + limit]]


# --- Exports (T3.4) ---


@router.get("/{case_id}/export")
async def export_case(
    case_id: uuid.UUID,
    fmt: Annotated[str, Query(pattern="^(json|csv|zip)$")],
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    case = await _scoped_case(session, case_id, user)
    evidence = await list_case_evidence(session, case.id)

    await append_audit(
        session,
        actor_id=uuid.UUID(user.id),
        action="case.export",
        target=str(case.id),
        payload={"fmt": fmt},
    )
    await session.commit()

    # `case.number` is validated on write (see CaseCreate) to be free of quotes
    # and newlines, so interpolating it into Content-Disposition is safe.
    content, media, ext = {
        "json": (export_json(case, evidence), "application/json", "json"),
        "csv": (export_csv(evidence), "text/csv", "csv"),
        "zip": (export_zip(case, evidence), "application/zip", "zip"),
    }[fmt]
    return Response(
        content=content,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{case.number}.{ext}"'},
    )
