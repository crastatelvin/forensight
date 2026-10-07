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
import logging
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
from app.auth.service import mutation_rate_limit, read_rate_limit
from app.config import Settings, get_settings
from app.db import get_session
from app.domain import redaction
from app.domain.access import scoped_case, visible_case
from app.domain.antivirus import ClamAVScanner, InfectedFileError, NoopScanner, Scanner
from app.domain.exports import export_csv, export_json, export_zip
from app.domain.ingest import EvidenceValidationError
from app.domain.links import describe, find_links
from app.domain.models import AuditLog, Case, Evidence, Finding
from app.domain.notifications import Notifier
from app.domain.redaction import RedactionError
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
    FindingCitationRead,
    FindingCreate,
    FindingRead,
    FindingUpdate,
    TimelineEventCreate,
    TimelineEventRead,
    case_to_read,
)
from app.domain.service import IngestService
from app.domain.state_machine import IllegalTransitionError, assert_transition
from app.domain.storage import ObjectStore
from app.domain.workflow_repo import (
    citations_for_findings,
    cite_results,
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
    dependencies=[Depends(read_rate_limit)],
)

logger = logging.getLogger("forensight")

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


def _derived_id(source_ref: str) -> uuid.UUID:
    """A stable UUID for a derived event, which has no row to have an id.

    Derived from the source reference so the same record produces the same id on
    every request. That matters for more than tidiness: React keys the timeline list
    by `id`, and an id regenerated per request would remount every entry on each
    refresh, discarding focus and any text selection the examiner had.

    Namespaced by the prefix so it cannot collide with a real row id, which would let
    a derived entry be mistaken for a recorded one by anything that only compares ids.
    """
    return uuid.uuid5(_DERIVED_ID_NAMESPACE, source_ref)


#: Fixed namespace for `_derived_id`. A literal rather than a random per-process UUID:
#: the ids have to match across processes and across restarts.
_DERIVED_ID_NAMESPACE = uuid.UUID("6f1d5a6e-0f3a-5c2b-9a4e-1d7c8b0f2a31")


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


@router.post(
    "", response_model=CaseRead, status_code=201, dependencies=[Depends(mutation_rate_limit)]
)
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
    return case_to_read(case)


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
    return [case_to_read(c) for c in cases]


@router.get("/{case_id}", response_model=CaseRead)
async def read_case(  # noqa: D401 - must stay after the literal paths above
    case_id: uuid.UUID,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CaseRead:
    case = await scoped_case(session, case_id, user)
    return case_to_read(case)


@router.patch("/{case_id}", response_model=CaseRead, dependencies=[Depends(mutation_rate_limit)])
async def update_case(
    case_id: uuid.UUID,
    payload: CaseUpdate,
    user: Annotated[CurrentUser, Depends(require_roles(*MUTATING_ROLES))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CaseRead:
    case = await scoped_case(session, case_id, user)
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        return case_to_read(case)
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
    return case_to_read(case)


@router.post(
    "/{case_id}/transition", response_model=CaseRead, dependencies=[Depends(mutation_rate_limit)]
)
async def transition_case(
    case_id: uuid.UUID,
    payload: CaseTransition,
    user: Annotated[CurrentUser, Depends(require_roles(*MUTATING_ROLES))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CaseRead:
    case = await scoped_case(session, case_id, user)
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
    return case_to_read(case)


# --- Evidence ---


@router.post(
    "/{case_id}/evidence",
    response_model=EvidenceRead,
    status_code=201,
    dependencies=[Depends(mutation_rate_limit)],
)
async def upload_evidence(
    case_id: uuid.UUID,
    user: Annotated[CurrentUser, Depends(require_roles(*MUTATING_ROLES))],
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    file: Annotated[UploadFile, File()],
) -> EvidenceRead:
    case = await scoped_case(session, case_id, user)

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
    case = await scoped_case(session, case_id, user)
    items = await list_case_evidence(session, case.id, limit=_limit(limit), offset=offset)
    return [EvidenceRead.model_validate(e) for e in items]


@router.get("/evidence/{evidence_id}/download")
async def download_evidence(
    evidence_id: uuid.UUID,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    disposition: Annotated[str, Query(pattern="^(attachment|inline)$")] = "attachment",
) -> Response:
    """Serve the original bytes of one piece of evidence.

    `disposition=inline` exists for the in-app viewer, which has to render the
    image rather than save it -- `attachment` alone makes every `<img>` in the UI a
    failed load, which is a viewer that silently shows nothing. Download stays the
    default, because that is what the endpoint is for and because rendering
    untrusted bytes inline is the less safe of the two.

    The bytes are the original file, hash-verified in `service.download` before
    they are returned; a mismatch is a 409 with an audit row, never a subtly
    altered image.
    """
    # T2.2: object-id only; no path input is ever accepted.
    evidence = await _scoped_evidence(session, evidence_id, user)
    service = _ingest_service(settings)
    try:
        evidence, data = await service.download(
            session, evidence_id=evidence.id, actor_id=uuid.UUID(user.id)
        )
    except FileNotFoundError as exc:
        # 410, not 404. The record exists and the bytes do not, which is an
        # integrity incident -- and answering 404 would make it indistinguishable
        # from a mistyped id, so nobody would go looking. 410 says "this was here
        # and is now permanently unavailable", which is the truth.
        logger.error("evidence %s has a record but no stored bytes: %s", evidence_id, exc)
        raise HTTPException(status_code=410, detail=str(exc)) from exc
    except ValueError as exc:
        # The integrity-failure audit row was flushed but not committed; without
        # this commit the one event that most needs to be durable is rolled back.
        await session.commit()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await session.commit()
    return Response(
        content=data,
        media_type=evidence.content_type,
        headers={
            "Content-Disposition": f'{disposition}; filename="{evidence.filename}"',
            # Evidence is immutable, so the bytes for a given id never change.
            # Caching them is safe and makes the viewer instant on re-open.
            "Cache-Control": "private, max-age=3600, immutable",
        },
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


@router.post(
    "/{case_id}/assign", response_model=CaseRead, dependencies=[Depends(mutation_rate_limit)]
)
async def assign_case(
    case_id: uuid.UUID,
    payload: CaseAssign,
    user: Annotated[CurrentUser, Depends(require_roles(Role.ADMIN, Role.OFFICER))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CaseRead:
    case = await scoped_case(session, case_id, user)
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
    return case_to_read(case)


# --- Findings ---


@router.post(
    "/{case_id}/findings",
    response_model=FindingRead,
    status_code=201,
    dependencies=[Depends(mutation_rate_limit)],
)
async def add_finding(
    case_id: uuid.UUID,
    payload: FindingCreate,
    user: Annotated[CurrentUser, Depends(require_roles(*MUTATING_ROLES))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> FindingRead:
    case = await scoped_case(session, case_id, user)
    finding = await create_finding(
        session,
        case_id=case.id,
        title=payload.title,
        body=payload.body,
        created_by=uuid.UUID(user.id),
    )
    citations, rejected = await cite_results(
        session, finding=finding, case_id=case.id, result_ids=payload.citations
    )
    if rejected:
        # Refused rather than stored. A citation that resolved to nothing would be
        # rendered in a signed report as a reference to a result, and a reader
        # checking it would find nothing — the report would be making a claim about
        # provenance that is false.
        await session.rollback()
        raise HTTPException(
            status_code=422,
            detail=(
                "these citation ids are not analyzer results on this case: "
                + ", ".join(str(r) for r in rejected)
            ),
        )
    await append_audit(
        session,
        actor_id=uuid.UUID(user.id),
        action="finding.create",
        target=str(finding.id),
        payload={
            "case_id": str(case.id),
            "cited_result_ids": [str(c.result_id) for c in citations],
        },
    )
    await session.commit()
    return await finding_read(session, finding)


async def finding_read(session: AsyncSession, finding: Finding) -> FindingRead:
    """A finding with its citations attached.

    The citations are loaded here rather than as a SQLAlchemy relationship because a
    finding's sourcing has to be *explicit* wherever the finding appears. A lazy
    relationship means whether sourcing is visible depends on whether something
    touched it, and a finding that looks sourced on one screen and unsourced on the
    next is worse than either.
    """
    citations = await citations_for_findings(session, [finding.id])
    read = FindingRead.model_validate(finding)
    read.citations = [FindingCitationRead.model_validate(c) for c in citations.get(finding.id, [])]
    return read


@router.get("/{case_id}/findings", response_model=list[FindingRead])
async def get_findings(
    case_id: uuid.UUID,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=_MAX_PAGE)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[FindingRead]:
    case = await scoped_case(session, case_id, user)
    rows = await list_findings(session, case.id, limit=_limit(limit), offset=offset)
    out: list[FindingRead] = []
    for row in rows:
        out.append(await finding_read(session, row))
    return out
    return [FindingRead.model_validate(f) for f in rows]


@router.patch(
    "/{case_id}/findings/{finding_id}",
    response_model=FindingRead,
    dependencies=[Depends(mutation_rate_limit)],
)
async def edit_finding(
    case_id: uuid.UUID,
    finding_id: uuid.UUID,
    payload: FindingUpdate,
    user: Annotated[CurrentUser, Depends(require_roles(*MUTATING_ROLES))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> FindingRead:
    case = await scoped_case(session, case_id, user)
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
    return await finding_read(session, finding)


@router.delete(
    "/{case_id}/findings/{finding_id}", status_code=204, dependencies=[Depends(mutation_rate_limit)]
)
async def remove_finding(
    case_id: uuid.UUID,
    finding_id: uuid.UUID,
    user: Annotated[CurrentUser, Depends(require_roles(*MUTATING_ROLES))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    case = await scoped_case(session, case_id, user)
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


@router.post(
    "/{case_id}/entities",
    response_model=EntityRead,
    status_code=201,
    dependencies=[Depends(mutation_rate_limit)],
)
async def add_entity(
    case_id: uuid.UUID,
    payload: EntityCreate,
    user: Annotated[CurrentUser, Depends(require_roles(*MUTATING_ROLES))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> EntityRead:
    case = await scoped_case(session, case_id, user)
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
    case = await scoped_case(session, case_id, user)
    rows = await list_entities(session, case.id, limit=_limit(limit), offset=offset)
    return [EntityRead.model_validate(e) for e in rows]


@router.get("/{case_id}/entities/links")
async def entity_links(
    case_id: uuid.UUID,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=_MAX_PAGE)] = 100,
) -> dict[str, Any]:
    """Entities on this case that also appear on another case the caller can see.

    **Only cases the caller may already open are read or reported.** A co-occurrence
    involving an invisible case is omitted entirely rather than hinted at — reporting
    "this name also appears somewhere you cannot see" would confirm both the existence
    and the content of a case the caller was refused, which is what the 404-on-
    out-of-scope rule exists to prevent. The scope predicate is applied in SQL, so
    unauthorised rows are never loaded.

    A link is a **candidate, not an identification**: two people can share a name.
    Matching is exact on a case-folded, whitespace-collapsed value, and no fuzzy
    comparison is attempted — see `app/domain/links.py` for why a confidence score on a
    guess is worse than no link.
    """
    case = await scoped_case(session, case_id, user)
    found = await find_links(session, case_id=case.id, user=user, limit=_limit(limit))

    await append_audit(
        session,
        actor_id=uuid.UUID(user.id),
        action="entity.links.read",
        target=str(case.id),
        # The count, not the values: who searched for cross-case links is itself a
        # record worth keeping, and a link search is exactly the kind of act that
        # should not go unlogged even though its results are legitimately visible.
        payload={"case_id": str(case.id), "link_count": len(found)},
    )
    await session.commit()
    return {
        "case_id": str(case.id),
        "links": [link.to_dict() for link in found],
        "count": len(found),
        **describe(),
    }


@router.delete(
    "/{case_id}/entities/{entity_id}", status_code=204, dependencies=[Depends(mutation_rate_limit)]
)
async def remove_entity(
    case_id: uuid.UUID,
    entity_id: uuid.UUID,
    user: Annotated[CurrentUser, Depends(require_roles(*MUTATING_ROLES))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    case = await scoped_case(session, case_id, user)
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


@router.post(
    "/{case_id}/timeline",
    response_model=TimelineEventRead,
    status_code=201,
    dependencies=[Depends(mutation_rate_limit)],
)
async def add_timeline_event(
    case_id: uuid.UUID,
    payload: TimelineEventCreate,
    user: Annotated[CurrentUser, Depends(require_roles(*MUTATING_ROLES))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TimelineEventRead:
    case = await scoped_case(session, case_id, user)
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
    derived: Annotated[bool, Query()] = False,
) -> list[TimelineEventRead]:
    """The case timeline: the examiner's own entries plus what the case records.

    `derived=False` (the default) returns only what an examiner entered. The derived
    view is opt-in, because these entries describe *handling* — when this platform
    received and touched evidence — and an examiner building a chronology needs to be
    able to exclude them rather than sift them out.
    """
    case = await scoped_case(session, case_id, user)
    rows = await list_timeline(session, case.id, limit=_limit(limit), offset=offset)

    if not derived:
        return [TimelineEventRead.model_validate(t) for t in rows]

    evidence = {e.id: e for e in await list_case_evidence(session, case.id, limit=_limit(limit))}
    from app.domain.timeline import derive_events, merge, recorded_undated

    derived_events = await derive_events(session, case.id, evidence_by_id=evidence)
    combined = merge(derived_events, rows)

    out: list[TimelineEventRead] = []
    for entry in combined[: _limit(limit)]:
        out.append(
            TimelineEventRead(
                # A recorded entry keeps its real row id; only a derived one — which
                # has no row — gets a synthetic id derived from its source reference.
                id=entry.id or _derived_id(entry.source_ref),
                case_id=case.id,
                occurred_at=entry.occurred_at,
                label=entry.label,
                source_ref=entry.source_ref,
                # A derived entry is never the examiner's own statement, so it is
                # never `uncertain` in their sense — and marking it uncertain would
                # dilute the flag that exists to mean something.
                uncertain=False,
                created_at=entry.occurred_at,
                derived=entry.derived,
            )
        )
    # Undated examiner entries go last, and keep `uncertain` as recorded.
    for row in recorded_undated(rows):
        out.append(TimelineEventRead.model_validate(row))
    return out


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
    return [case_to_read(c) for c in results[offset : offset + limit]]


# --- Exports (T3.4) ---


@router.get("/{case_id}/export")
async def export_case(
    case_id: uuid.UUID,
    fmt: Annotated[str, Query(pattern="^(json|csv|zip)$")],
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    redact: Annotated[str | None, Query()] = None,
    redact_text: Annotated[str | None, Query()] = None,
) -> Response:
    """Export a case as JSON, CSV or a ZIP manifest.

    `redact=owner_id,assigned_to` removes those fields and `redact_text` takes
    newline-separated literal strings to scrub from free text. Every export is checked
    against the values it removed before being returned; a redaction that did not take
    effect is refused rather than sent.

    **Redaction is deliberately narrow**: named fields and literal strings only. There
    is no pattern matching, so this cannot claim to have found PII it did not find, and
    free text is passed through unless a caller names the exact string in it.
    """
    case = await scoped_case(session, case_id, user)
    evidence = await list_case_evidence(session, case.id)

    try:
        rule = redaction.parse(redact, redact_text)
    except RedactionError as exc:
        # 422, not 400: the request was well-formed but names something that cannot
        # be redacted, and a silent pass-through would be the dangerous answer.
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # A redacted export is not evidence and is never signed as a report, so it is
    # recorded distinctly in the audit trail rather than as an ordinary export.
    await append_audit(
        session,
        actor_id=uuid.UUID(user.id),
        action="case.export.redacted" if rule.active else "case.export",
        target=str(case.id),
        payload={"fmt": fmt, **rule.describe()},
    )
    await session.commit()

    # `case.number` is validated on write (see CaseCreate) to be free of quotes
    # and newlines, so interpolating it into Content-Disposition is safe.
    try:
        content, media, ext = {
            "json": (export_json(case, evidence, redaction=rule), "application/json", "json"),
            "csv": (export_csv(evidence, redaction=rule), "text/csv", "csv"),
            "zip": (export_zip(case, evidence, redaction=rule), "application/zip", "zip"),
        }[fmt]
    except RedactionError as exc:
        # A 500, deliberately. Reaching here means the exporter produced a document
        # still containing a value the caller asked to have removed, which is a bug in
        # this codebase rather than anything the requester did wrong — and it must not
        # be reported as a client error, because the client did ask for redaction.
        logger.error("redaction failed for case %s: %s", case.id, exc)
        raise HTTPException(
            status_code=500,
            detail="redaction did not take effect; the export was refused rather than sent",
        ) from exc

    headers = {"Content-Disposition": f'attachment; filename="{case.number}.{ext}"'}
    if rule.active:
        # Stated in the response, so a document separated from this request still
        # carries the fact that it is incomplete.
        headers["X-Redacted-Fields"] = ",".join(sorted(rule.fields))
        headers["X-Redacted-Notice"] = (
            "redacted export: not evidence, not signed; the case file remains the "
            "record of this case"
        )
    return Response(content=content, media_type=media, headers=headers)
