"""Findings, entities, timeline repository helpers (Phase 3)."""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models import Entity, Finding, TimelineEvent


async def create_finding(
    session: AsyncSession, *, case_id: uuid.UUID, title: str, body: str,
    created_by: uuid.UUID,
) -> Finding:
    finding = Finding(
        case_id=case_id, title=title, body=body, created_by=created_by
    )
    session.add(finding)
    await session.flush()
    return finding


async def list_findings(
    session: AsyncSession, case_id: uuid.UUID
) -> list[Finding]:
    result = await session.execute(
        select(Finding).where(Finding.case_id == case_id).order_by(
            Finding.created_at.asc()
        )
    )
    return list(result.scalars().all())


async def create_entity(
    session: AsyncSession, *, case_id: uuid.UUID, kind: str, value: str,
    attributes: dict[str, object] | None = None,
) -> Entity:
    entity = Entity(
        case_id=case_id, kind=kind, value=value, attributes=attributes or {}
    )
    session.add(entity)
    await session.flush()
    return entity


async def list_entities(
    session: AsyncSession, case_id: uuid.UUID
) -> list[Entity]:
    result = await session.execute(
        select(Entity).where(Entity.case_id == case_id).order_by(
            Entity.created_at.asc()
        )
    )
    return list(result.scalars().all())


async def list_timeline(
    session: AsyncSession, case_id: uuid.UUID
) -> list[TimelineEvent]:
    result = await session.execute(
        select(TimelineEvent)
        .where(TimelineEvent.case_id == case_id)
        .order_by(TimelineEvent.occurred_at.asc().nullslast())
    )
    return list(result.scalars().all())
