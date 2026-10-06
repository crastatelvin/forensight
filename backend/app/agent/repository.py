"""Agent persistence: runs, checkpoints, tool-call audit, memory (P5.B3/B4/B6).

`agent_runs` holds one row per run and is the resumable unit of work. State lives
in `agent_checkpoints` as an append-only history rather than an overwrite, so
"where was it when it died" is answerable after the fact — which is the same
reason the evidence audit log is append-only.

`agent_memories` carries pgvector embeddings. Scoping is (case_id, actor_id):
rows are only ever read through a scope predicate, never by id alone, so a
forged id in a tool argument cannot widen visibility.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON, TypeDecorator

from app.db import Base
from app.domain.models import JSONType, utcnow

#: pgvector column width.
#:
#: This must equal the width the configured embedding model actually returns,
#: because the column width is fixed by migration 0006. Measured, not assumed:
#: `bge-m3` served by Ollama returns 1024 dims and `nomic-embed-text` returns
#: 768. 1024 is pinned because bge-m3 is the retrieval model the plan calls for.
#: Width is validated on write, so a mismatched model fails loudly at the point
#: of insertion instead of silently degrading recall.
#:
#: Changing this means a migration to widen the column — it is not a config
#: tweak.
EMBED_DIM = 1024


class EmbeddingVector(TypeDecorator[Any]):
    """pgvector column on Postgres, JSON list elsewhere.

    Uses pgvector's own SQLAlchemy type on Postgres rather than a hand-rolled
    binding. An earlier attempt declared `ARRAY(FLOAT)` and bound a Python list,
    which passes every SQLite test and then fails at runtime on Postgres with
    "Unknown PG numeric type" — asyncpg has no codec for a `vector` column it
    was not told about. `Vector` renders the right DDL, binds the right literal,
    and registers the codec.

    The SQLite/JSON branch keeps `create_all` working in tests. Width is checked
    on bind so a mismatched embedding model fails at the write with a clear
    message instead of degrading recall silently.
    """

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect: Any) -> Any:
        if dialect.name == "postgresql":
            from pgvector.sqlalchemy import Vector

            return dialect.type_descriptor(Vector(EMBED_DIM))
        return dialect.type_descriptor(JSON())

    def process_bind_param(self, value: Any, dialect: Any) -> Any:
        # NULL is legitimate: text-only memory (embedding provider down) is still
        # worth keeping, it just cannot be recalled by similarity. A non-empty
        # vector of the wrong width is a real configuration error.
        if value is None or len(value) == 0:
            return None
        if len(value) != EMBED_DIM:
            raise ValueError(
                f"embedding has dimension {len(value)}, expected {EMBED_DIM}; the "
                f"configured embedding model does not match this deployment"
            )
        return list(value)

    def process_result_value(self, value: Any, dialect: Any) -> Any:
        if value is None:
            return []
        return [float(v) for v in value]


def json_type() -> Any:
    """JSON on every dialect, JSONB on Postgres. JSONB() is untyped upstream."""
    return JSON().with_variant(JSONB(), "postgresql")  # type: ignore[no-untyped-call]


def _vector_column() -> Mapped[list[float] | None]:
    # Nullable: a memory can exist without a vector when the embedding provider
    # is unavailable. Similarity recall skips those rows; recency recall keeps
    # them, which is the behaviour we want — the note is lost otherwise.
    return mapped_column(EmbeddingVector(), default=None, nullable=True)


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    thread_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    step: Mapped[str] = mapped_column(String(32), default="intake", nullable=False)
    objective: Mapped[str] = mapped_column(Text, default="", nullable=False)
    report: Mapped[str] = mapped_column(Text, default="", nullable=False)
    refusal: Mapped[str] = mapped_column(Text, default="", nullable=False)
    error: Mapped[str] = mapped_column(Text, default="", nullable=False)
    #: LLM provider+model that produced this run. Recorded so a report can say
    #: which model was responsible for its narrative.
    model: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    requested_by: Mapped[uuid.UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=utcnow,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
    )


class AgentCheckpoint(Base):
    """Append-only state history. Latest row per (run_id, step) is the truth."""

    __tablename__ = "agent_checkpoints"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    step: Mapped[str] = mapped_column(String(32), nullable=False)
    state: Mapped[dict[str, Any]] = mapped_column(json_type(), default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=utcnow,
        nullable=False,
    )


Index("ix_agent_checkpoints_run_step", AgentCheckpoint.run_id, AgentCheckpoint.step)


class AgentToolCall(Base):
    """Audit of every tool call, including rejected ones (T5.2)."""

    __tablename__ = "agent_tool_calls"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    call_id: Mapped[str] = mapped_column(String(64), nullable=False)
    tool: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    args: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    args_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    result_hash: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    ok: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    error: Mapped[str] = mapped_column(Text, default="", nullable=False)
    #: Set when this call ran after a human approval, linking it to the approval
    #: that authorised it.
    approved_action_id: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=utcnow,
        nullable=False,
    )


class AgentMemory(Base):
    """Episodic + semantic memory (P5.B4).

    `kind` separates the two: `episodic` is what happened in a run, `semantic` is
    durable knowledge. Both are scoped, both are embedded.
    """

    __tablename__ = "agent_memories"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    actor_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    #: Embedding as a plain list; migration 0006 upgrades this to a pgvector
    #: column with a cosine-distance index.
    embedding: Mapped[list[float] | None] = _vector_column()
    dim: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: Set when sanitisation found injection-shaped content in the memory text.
    injection_findings: Mapped[list[str]] = mapped_column(
        json_type(),
        default=list,
        nullable=False,
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=utcnow,
        nullable=False,
    )


# Recall query: scoped by case + actor, ranked by embedding similarity.
Index(
    "ix_agent_memories_scope_kind",
    AgentMemory.case_id,
    AgentMemory.actor_id,
    AgentMemory.kind,
    AgentMemory.created_at.desc(),
)


# --- Run + checkpoint ---


async def create_run(
    session: AsyncSession,
    *,
    case_id: uuid.UUID,
    thread_id: str,
    objective: str,
    actor_id: uuid.UUID,
    model: str,
) -> AgentRun:
    run = AgentRun(
        case_id=case_id,
        thread_id=thread_id,
        objective=objective,
        requested_by=actor_id,
        model=model,
    )
    session.add(run)
    await session.flush()
    return run


async def get_run(session: AsyncSession, run_id: uuid.UUID) -> AgentRun | None:
    return await session.get(AgentRun, run_id)


async def update_run(session: AsyncSession, run: AgentRun, **fields: Any) -> AgentRun:
    for key, value in fields.items():
        setattr(run, key, value)
    run.updated_at = datetime.now(UTC)
    await session.flush()
    return run


async def save_checkpoint(
    session: AsyncSession, *, run_id: uuid.UUID, step: str, state: dict[str, Any]
) -> AgentCheckpoint:
    cp = AgentCheckpoint(run_id=run_id, step=step, state=state)
    session.add(cp)
    await session.flush()
    return cp


async def latest_checkpoint(session: AsyncSession, run_id: uuid.UUID) -> AgentCheckpoint | None:
    from sqlalchemy import select

    result = await session.execute(
        select(AgentCheckpoint)
        .where(AgentCheckpoint.run_id == run_id)
        .order_by(AgentCheckpoint.id.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def list_checkpoints(session: AsyncSession, run_id: uuid.UUID) -> list[AgentCheckpoint]:
    from sqlalchemy import select

    result = await session.execute(
        select(AgentCheckpoint)
        .where(AgentCheckpoint.run_id == run_id)
        .order_by(AgentCheckpoint.id.asc())
    )
    return list(result.scalars().all())


# --- Tool-call audit (T5.2) ---


async def record_tool_call(
    session: AsyncSession,
    *,
    run_id: uuid.UUID,
    call: Any,
    result: Any,
    case_id: uuid.UUID,
    approved_action_id: str = "",
) -> AgentToolCall:
    from app.agent.types import ToolCall, ToolResult

    # TypeError rather than assert: `assert` is stripped under `python -O`, so a
    # mis-typed call would slip through to an AttributeError several lines later,
    # in a function whose entire job is writing a trustworthy audit row. A missing
    # check here is worse than a crash -- it means a tool call could be recorded
    # without the arguments or result hash the audit depends on.
    if not isinstance(call, ToolCall):
        raise TypeError(f"call must be a ToolCall, got {type(call).__name__}")
    if not isinstance(result, ToolResult):
        raise TypeError(f"result must be a ToolResult, got {type(result).__name__}")
    if not result.result_hash:
        result.compute_hash()
    row = AgentToolCall(
        # ToolRunner carries these as strings (they come from request paths);
        # SQLAlchemy's UUID columns need real UUIDs.
        run_id=uuid.UUID(str(run_id)),
        case_id=uuid.UUID(str(case_id)),
        call_id=call.call_id,
        tool=call.name,
        args=call.args,
        args_hash=call.args_hash(),
        result_hash=result.result_hash,
        ok=result.ok,
        error=result.error,
        approved_action_id=approved_action_id,
    )
    session.add(row)
    await session.flush()
    return row


async def list_tool_calls(session: AsyncSession, run_id: uuid.UUID) -> list[AgentToolCall]:
    from sqlalchemy import select

    result = await session.execute(
        select(AgentToolCall)
        .where(AgentToolCall.run_id == run_id)
        .order_by(AgentToolCall.created_at.asc())
    )
    return list(result.scalars().all())


async def list_runs(
    session: AsyncSession, case_id: uuid.UUID, *, limit: int = 50
) -> list[AgentRun]:
    """Every run on a case, newest first.

    Needed because a run's output is durable and must stay readable after the
    browser that started it is closed. Without this the console showed "No run yet"
    on every fresh page load, which for a forensic tool means the agent's report,
    its citations and its tool-call audit trail all disappeared the moment you
    reloaded — the worst possible behaviour for a record that is meant to be
    evidence of what was done.
    """
    from sqlalchemy import select

    result = await session.execute(
        select(AgentRun)
        .where(AgentRun.case_id == case_id)
        .order_by(AgentRun.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


# --- Memory (P5.B4/T5.6) ---


async def write_memory(
    session: AsyncSession,
    *,
    case_id: uuid.UUID,
    actor_id: uuid.UUID,
    kind: str,
    content: str,
    embedding: list[float],
    injection_findings: list[str] | None = None,
    run_id: uuid.UUID | None = None,
) -> AgentMemory:
    """Store a memory. Embeddings of the wrong width are rejected here rather
    than failing later inside a distance calculation."""
    if embedding and len(embedding) != EMBED_DIM:
        raise ValueError(
            f"embedding has dimension {len(embedding)}, expected {EMBED_DIM}; "
            f"the configured embedding model does not match this deployment"
        )
    row = AgentMemory(
        case_id=case_id,
        actor_id=actor_id,
        kind=kind,
        content=content,
        embedding=embedding,
        dim=len(embedding),
        injection_findings=injection_findings or [],
        run_id=run_id,
    )
    session.add(row)
    await session.flush()
    return row


def _scope_filter(*, case_id: uuid.UUID, actor_id: uuid.UUID, kind: str = "") -> list[Any]:
    """The only way memories are ever selected.

    Scope is part of the query, not a post-filter, so there is no code path that
    returns a memory from another case even if a caller passes the wrong id.
    """
    filters = [AgentMemory.case_id == case_id, AgentMemory.actor_id == actor_id]
    if kind:
        filters.append(AgentMemory.kind == kind)
    return filters


async def recall_memory(
    session: AsyncSession,
    *,
    case_id: uuid.UUID,
    actor_id: uuid.UUID,
    query_embedding: list[float] | None = None,
    kind: str = "",
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Recall memories by cosine similarity, scoped to case + actor.

    On Postgres this uses pgvector's `<=>` operator with a HNSW index. Elsewhere
    (SQLite tests) it falls back to Python cosine similarity over the scoped
    rows — slower, but the scope predicate is identical, so a test cannot pass
    here and fail in production.
    """
    from sqlalchemy import select

    filters = _scope_filter(case_id=case_id, actor_id=actor_id, kind=kind)
    dialect = session.bind.dialect.name if session.bind else ""
    if dialect == "postgresql" and query_embedding:
        # pgvector's `<=>` is cosine *distance* (0 = identical).
        rows = await session.execute(
            select(
                AgentMemory,
                text("embedding <=> CAST(:vec AS vector) AS distance"),
            )
            .where(*filters)
            .where(AgentMemory.embedding.is_not(None))
            .order_by(text("embedding <=> CAST(:vec AS vector)"))
            .limit(limit),
            {"vec": list(query_embedding)},
        )
        return [{**_memory_dict(row[0]), "distance": float(row[1])} for row in rows.all()]

    result = await session.execute(
        select(AgentMemory).where(*filters).order_by(AgentMemory.created_at.desc())
    )
    rows = list(result.scalars().all())
    if query_embedding:
        scored = [
            {**_memory_dict(r), "distance": _cosine(query_embedding, r.embedding)}
            for r in rows
            if r.embedding
        ]
        scored.sort(key=lambda m: m["distance"])
        return scored[:limit]
    return [{**_memory_dict(r), "distance": None} for r in rows[:limit]]


def _memory_dict(row: AgentMemory) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "kind": row.kind,
        "content": row.content,
        "case_id": str(row.case_id),
        "actor_id": str(row.actor_id),
        "injection_findings": row.injection_findings,
        "created_at": row.created_at.isoformat(),
    }


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine distance in [0, 2]; 0 is identical.

    Hand-rolled rather than pulling in numpy: it is ~8 lines, and numpy would
    add a ~50 MB dependency for one dot product.
    """
    if not a or not b or len(a) != len(b):
        return 1.0
    dot: float = sum(x * y for x, y in zip(a, b, strict=False))
    na: float = sum(x * x for x in a) ** 0.5
    nb: float = sum(y * y for y in b) ** 0.5
    if na == 0.0 or nb == 0.0:
        return 1.0
    similarity: float = dot / (na * nb)
    return 1.0 - similarity
