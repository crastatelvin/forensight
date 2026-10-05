"""Data-access + service helpers for cases, evidence, custody, audit (Phase 2)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import column, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.auth.rbac import CurrentUser
from app.domain.access import case_scope_filter
from app.domain.audit import GENESIS_HASH, compute_entry_hash
from app.domain.models import (
    AuditLog,
    Case,
    ChainOfCustody,
    Evidence,
)

# --- Audit log (append-only, hash-chained) ---

# Arbitrary but fixed key so every process contends on the same lock.
_AUDIT_LOCK_KEY = 77881123


async def _lock_audit_chain(session: AsyncSession) -> None:
    """Serialize audit appends so concurrent writers cannot fork the chain.

    Without this, two requests can both read the same tail `prev_hash` and
    insert siblings, after which `verify_audit_chain` fails forever. A
    transaction-scoped advisory lock is the cheapest correct fix; SQLite (tests)
    is single-writer so it is a no-op there.
    """
    dialect = session.bind.dialect.name if session.bind else ""
    if dialect == "postgresql":
        await session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _AUDIT_LOCK_KEY})


async def _latest_audit_hash(session: AsyncSession) -> str:
    result = await session.execute(
        select(AuditLog.entry_hash).order_by(AuditLog.id.desc()).limit(1)
    )
    row = result.scalar_one_or_none()
    return row or GENESIS_HASH


async def append_audit(
    session: AsyncSession,
    *,
    actor_id: uuid.UUID | None,
    action: str,
    target: str = "",
    payload: dict[str, object] | None = None,
) -> AuditLog:
    payload = payload or {}
    await _lock_audit_chain(session)
    prev_hash = await _latest_audit_hash(session)
    entry_hash = compute_entry_hash(
        prev_hash=prev_hash,
        actor_id=str(actor_id) if actor_id else None,
        action=action,
        target=target,
        payload=payload,
    )
    entry = AuditLog(
        actor_id=actor_id,
        action=action,
        target=target,
        payload=payload,
        prev_hash=prev_hash,
        entry_hash=entry_hash,
    )
    session.add(entry)
    await session.flush()
    return entry


async def verify_audit_chain(session: AsyncSession) -> bool:
    """Recompute the whole chain; returns False if any row was tampered (T2.5)."""
    result = await session.execute(select(AuditLog).order_by(AuditLog.id.asc()))
    prev = GENESIS_HASH
    for entry in result.scalars().all():
        if entry.prev_hash != prev:
            return False
        expected = compute_entry_hash(
            prev_hash=entry.prev_hash,
            actor_id=str(entry.actor_id) if entry.actor_id else None,
            action=entry.action,
            target=entry.target,
            payload=entry.payload,
        )
        if expected != entry.entry_hash:
            return False
        prev = entry.entry_hash
    return True


async def list_audit(session: AsyncSession, *, limit: int = 50, offset: int = 0) -> list[AuditLog]:
    """Newest-first page of audit entries, for the admin audit view."""
    result = await session.execute(
        select(AuditLog).order_by(AuditLog.id.desc()).limit(limit).offset(offset)
    )
    return list(result.scalars().all())


# --- Custody ---


async def record_custody(
    session: AsyncSession,
    *,
    evidence_id: uuid.UUID,
    actor_id: uuid.UUID,
    action: str,
    detail: str = "",
) -> ChainOfCustody:
    entry = ChainOfCustody(evidence_id=evidence_id, actor_id=actor_id, action=action, detail=detail)
    session.add(entry)
    await session.flush()
    return entry


async def custody_for_evidence(
    session: AsyncSession, evidence_id: uuid.UUID
) -> list[ChainOfCustody]:
    result = await session.execute(
        select(ChainOfCustody)
        .where(ChainOfCustody.evidence_id == evidence_id)
        .order_by(ChainOfCustody.created_at.asc(), ChainOfCustody.id.asc())
    )
    return list(result.scalars().all())


# --- Cases & evidence ---


async def get_case(session: AsyncSession, case_id: uuid.UUID) -> Case | None:
    return await session.get(Case, case_id)


async def get_evidence(session: AsyncSession, evidence_id: uuid.UUID) -> Evidence | None:
    return await session.get(Evidence, evidence_id)


async def find_evidence_by_hash(
    session: AsyncSession, case_id: uuid.UUID, sha256: str
) -> Evidence | None:
    result = await session.execute(
        select(Evidence).where(Evidence.case_id == case_id, Evidence.sha256 == sha256)
    )
    return result.scalar_one_or_none()


async def list_case_evidence(
    session: AsyncSession,
    case_id: uuid.UUID,
    *,
    limit: int = 100,
    offset: int = 0,
) -> list[Evidence]:
    result = await session.execute(
        select(Evidence)
        .where(Evidence.case_id == case_id)
        .order_by(Evidence.created_at.asc())
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars().all())


async def list_cases(
    session: AsyncSession,
    *,
    limit: int = 50,
    offset: int = 0,
    user: CurrentUser | None = None,
) -> list[Case]:
    """Newest-first page of cases visible to `user` (T3.1).

    Officers/experts see only cases they own or are assigned to; admins and
    reviewers see everything (reviewer stays read-only via RBAC on the routes).
    """
    stmt = select(Case).order_by(Case.created_at.desc())
    if user is not None:
        scope = case_scope_filter(user)
        if scope is not None:
            stmt = stmt.where(scope)
    result = await session.execute(stmt.limit(limit).offset(offset))
    return list(result.scalars().all())


async def search_cases(
    session: AsyncSession,
    query: str,
    *,
    limit: int = 50,
    user: CurrentUser | None = None,
) -> list[Case]:
    """Full-text search over case metadata (T3.3).

    Uses Postgres websearch_to_tsquery against the generated search_tsv column
    with ts_rank ordering. Parameter-bound, so injection attempts are inert.
    On non-Postgres (SQLite tests) falls back to a safe ILIKE scan.

    When `user` is supplied the result is narrowed to the cases that user may
    see — search must not become a visibility bypass.
    """
    dialect = session.bind.dialect.name if session.bind else ""
    scope = case_scope_filter(user) if user is not None else None

    if dialect == "postgresql":
        # `search_tsv` is a migration-managed generated column, so it is not
        # declared on the ORM model (that would break create_all on SQLite).
        # Referencing it as an ad-hoc column keeps the query fully composable —
        # including the visibility scope — while staying parameter-bound.
        tsv: ColumnElement[Any] = column("search_tsv")
        tsquery = func.websearch_to_tsquery("english", query)
        stmt = (
            select(Case)
            .where(tsv.op("@@")(tsquery))
            .order_by(func.ts_rank(tsv, tsquery).desc())
            .limit(limit)
        )
        if scope is not None:
            stmt = stmt.where(scope)
        result = await session.execute(stmt)
        return list(result.scalars().all())

    # SQLite fallback: parametrized LIKE (still injection-safe).
    like = f"%{query}%"
    stmt = select(Case).where(
        Case.title.ilike(like) | Case.description.ilike(like) | Case.number.ilike(like)
    )
    if scope is not None:
        stmt = stmt.where(scope)
    result = await session.execute(stmt.order_by(Case.created_at.desc()).limit(limit))
    return list(result.scalars().all())
