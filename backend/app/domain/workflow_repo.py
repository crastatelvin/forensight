"""Findings, entities, timeline repository helpers (Phase 3)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models import Entity, Finding, FindingCitation, TimelineEvent

# Evidence-backed findings can be published; everything else stays a draft.
FINDING_DRAFT = "draft"
FINDING_FINAL = "final"


async def create_finding(
    session: AsyncSession,
    *,
    case_id: uuid.UUID,
    title: str,
    body: str,
    created_by: uuid.UUID,
) -> Finding:
    finding = Finding(case_id=case_id, title=title, body=body, created_by=created_by)
    session.add(finding)
    await session.flush()
    return finding


async def cite_results(
    session: AsyncSession,
    *,
    finding: Finding,
    case_id: uuid.UUID,
    result_ids: list[uuid.UUID],
) -> tuple[list[FindingCitation], list[uuid.UUID]]:
    """Attach analyzer results to a finding as its warrant.

    Returns the citations created and the ids that were **rejected**, so the caller
    can tell the model which of its references did not resolve rather than silently
    dropping them. A finding whose citation was quietly discarded looks sourced and
    is not — which is the failure this whole path exists to prevent.

    A result belonging to another case is rejected rather than accepted: the check is
    the only thing stopping a finding on one case citing a result that exists only on
    another.
    """
    from app.analysis.models import AnalysisResult

    created: list[FindingCitation] = []
    rejected: list[uuid.UUID] = []
    if not result_ids:
        return created, rejected

    rows = (
        (
            await session.execute(
                select(AnalysisResult).where(
                    AnalysisResult.case_id == case_id,
                    AnalysisResult.id.in_(result_ids),
                )
            )
        )
        .scalars()
        .all()
    )
    by_id = {r.id: r for r in rows}

    for result_id in dict.fromkeys(result_ids):
        result = by_id.get(result_id)
        if result is None:
            rejected.append(result_id)
            continue
        status = str(result.payload.get("status", "")) if result.payload else ""
        citation = FindingCitation(
            finding_id=finding.id,
            result_id=result.id,
            evidence_id=result.evidence_id,
            module=result.module,
            score=result.score,
            threshold=result.threshold,
            tier=result.tier,
            model_version=result.model_version,
            # Snapshot the refusal, so the report can keep saying "could not be
            # measured" even after the analysis has been re-run and superseded.
            not_applicable=status == "not_applicable",
        )
        session.add(citation)
        created.append(citation)

    await session.flush()
    return created, rejected


async def citations_for_findings(
    session: AsyncSession, finding_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[FindingCitation]]:
    """Citations grouped by finding."""
    if not finding_ids:
        return {}
    rows = (
        (
            await session.execute(
                select(FindingCitation).where(FindingCitation.finding_id.in_(finding_ids))
            )
        )
        .scalars()
        .all()
    )
    grouped: dict[uuid.UUID, list[FindingCitation]] = {}
    for row in rows:
        grouped.setdefault(row.finding_id, []).append(row)
    return grouped


async def get_finding(session: AsyncSession, finding_id: uuid.UUID) -> Finding | None:
    return await session.get(Finding, finding_id)


async def update_finding(
    session: AsyncSession,
    finding: Finding,
    *,
    title: str | None = None,
    body: str | None = None,
    status: str | None = None,
) -> Finding:
    if title is not None:
        finding.title = title
    if body is not None:
        finding.body = body
    if status is not None:
        finding.status = status
    await session.flush()
    return finding


async def delete_finding(session: AsyncSession, finding: Finding) -> None:
    await session.delete(finding)
    await session.flush()


async def list_findings(
    session: AsyncSession, case_id: uuid.UUID, *, limit: int = 100, offset: int = 0
) -> list[Finding]:
    result = await session.execute(
        select(Finding)
        .where(Finding.case_id == case_id)
        .order_by(Finding.created_at.asc())
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars().all())


async def create_entity(
    session: AsyncSession,
    *,
    case_id: uuid.UUID,
    kind: str,
    value: str,
    attributes: dict[str, object] | None = None,
) -> Entity:
    entity = Entity(case_id=case_id, kind=kind, value=value, attributes=attributes or {})
    session.add(entity)
    await session.flush()
    return entity


async def get_entity(session: AsyncSession, entity_id: uuid.UUID) -> Entity | None:
    return await session.get(Entity, entity_id)


async def delete_entity(session: AsyncSession, entity: Entity) -> None:
    await session.delete(entity)
    await session.flush()


async def list_entities(
    session: AsyncSession, case_id: uuid.UUID, *, limit: int = 100, offset: int = 0
) -> list[Entity]:
    result = await session.execute(
        select(Entity)
        .where(Entity.case_id == case_id)
        .order_by(Entity.created_at.asc())
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars().all())


async def create_timeline_event(
    session: AsyncSession,
    *,
    case_id: uuid.UUID,
    label: str,
    occurred_at: datetime | None = None,
    source_ref: str = "",
    uncertain: bool = False,
) -> TimelineEvent:
    """Record one reconstructed event.

    `uncertain` is a first-class field, not a comment: a timeline entry the
    examiner is unsure about must be visibly uncertain in the export and UI.
    """
    event = TimelineEvent(
        case_id=case_id,
        label=label,
        occurred_at=occurred_at,
        source_ref=source_ref,
        uncertain=uncertain,
    )
    session.add(event)
    await session.flush()
    return event


async def list_timeline(
    session: AsyncSession, case_id: uuid.UUID, *, limit: int = 200, offset: int = 0
) -> list[TimelineEvent]:
    result = await session.execute(
        select(TimelineEvent)
        .where(TimelineEvent.case_id == case_id)
        .order_by(TimelineEvent.occurred_at.asc().nullslast())
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars().all())
