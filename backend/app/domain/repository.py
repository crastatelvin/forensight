"""Data-access + service helpers for cases, evidence, custody, audit (Phase 2)."""
from __future__ import annotations

import uuid

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.audit import GENESIS_HASH, compute_entry_hash
from app.domain.models import (
    AuditLog,
    Case,
    ChainOfCustody,
    Evidence,
)

# --- Audit log (append-only, hash-chained) ---

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


# --- Custody ---

async def record_custody(
    session: AsyncSession,
    *,
    evidence_id: uuid.UUID,
    actor_id: uuid.UUID,
    action: str,
    detail: str = "",
) -> ChainOfCustody:
    entry = ChainOfCustody(
        evidence_id=evidence_id, actor_id=actor_id, action=action, detail=detail
    )
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


async def get_evidence(
    session: AsyncSession, evidence_id: uuid.UUID
) -> Evidence | None:
    return await session.get(Evidence, evidence_id)


async def find_evidence_by_hash(
    session: AsyncSession, case_id: uuid.UUID, sha256: str
) -> Evidence | None:
    result = await session.execute(
        select(Evidence).where(
            Evidence.case_id == case_id, Evidence.sha256 == sha256
        )
    )
    return result.scalar_one_or_none()


async def list_case_evidence(
    session: AsyncSession, case_id: uuid.UUID
) -> list[Evidence]:
    result = await session.execute(
        select(Evidence)
        .where(Evidence.case_id == case_id)
        .order_by(Evidence.created_at.asc())
    )
    return list(result.scalars().all())


async def search_cases(
    session: AsyncSession, query: str, *, limit: int = 50
) -> list[Case]:
    """Full-text search over case metadata (T3.3).

    Uses Postgres websearch_to_tsquery against the generated search_tsv column
    with ts_rank ordering. Parameter-bound, so injection attempts are inert.
    On non-Postgres (SQLite tests) falls back to a safe ILIKE scan.
    """
    dialect = session.bind.dialect.name if session.bind else ""
    if dialect == "postgresql":
        stmt = text(
            "SELECT * FROM cases "
            "WHERE search_tsv @@ websearch_to_tsquery('english', :q) "
            "ORDER BY ts_rank(search_tsv, websearch_to_tsquery('english', :q)) DESC "
            "LIMIT :lim"
        )
        result = await session.execute(stmt, {"q": query, "lim": limit})
        rows = result.mappings().all()
        ids = [r["id"] for r in rows]
        if not ids:
            return []
        cases = {c.id: c for c in (await session.execute(
            select(Case).where(Case.id.in_(ids))
        )).scalars().all()}
        return [cases[i] for i in ids if i in cases]

    # SQLite fallback: parametrized LIKE (still injection-safe).
    like = f"%{query}%"
    result = await session.execute(
        select(Case)
        .where(
            Case.title.ilike(like)
            | Case.description.ilike(like)
            | Case.number.ilike(like)
        )
        .order_by(Case.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())

