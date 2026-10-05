"""Findings, entities, timeline repository helpers (Phase 3)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models import Entity, Finding, TimelineEvent

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
