"""Analysis job and result tables (P4.B2).

A job is the unit of work; a result is one module's output for one piece of
evidence. Results are kept separate from jobs so re-running a module appends a
new result rather than overwriting the old one — for a forensic platform, an
earlier conclusion being superseded must not erase that it ever existed.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.domain.models import JSONType, utcnow


class AnalysisJob(Base):
    """One queued/run unit of analysis.

    `idempotency_key` is UNIQUE. That constraint is the entire mechanism behind
    T4.6: concurrent submissions of identical work collapse because the second
    INSERT conflicts, not because of a check-then-insert that both racers pass.
    """

    __tablename__ = "analysis_jobs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    evidence_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evidence.id", ondelete="CASCADE"), nullable=False, index=True
    )
    module: Mapped[str] = mapped_column(String(64), nullable=False)
    model_version: Mapped[str] = mapped_column(String(64), nullable=False)
    #: hash(evidence_sha256 + module + params + model_version) — see types.py
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    status: Mapped[str] = mapped_column(String(16), default="queued", nullable=False)
    params: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    error: Mapped[str] = mapped_column(Text, default="", nullable=False)
    #: Set when status is `unsupported`, so the UI can explain *why* nothing ran.
    unsupported_reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    #: True when this job was served from a previous result instead of running.
    cached: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    requested_by: Mapped[uuid.UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=utcnow,
        nullable=False,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AnalysisResult(Base):
    """One module's output. Never mutated after insert."""

    __tablename__ = "analysis_results"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("analysis_jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    evidence_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evidence.id", ondelete="CASCADE"), nullable=False, index=True
    )
    module: Mapped[str] = mapped_column(String(64), nullable=False)
    #: sha256 of the (evidence, module, params, version) tuple that produced this
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    score: Mapped[float] = mapped_column(Float, nullable=False)
    #: Always carried alongside the score (P4.B6) — a score is meaningless alone.
    threshold: Mapped[float] = mapped_column(Float, nullable=False)
    model_version: Mapped[str] = mapped_column(String(64), nullable=False)
    tier: Mapped[str] = mapped_column(String(1), nullable=False)
    disclaimer: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=utcnow,
        nullable=False,
    )


# Case+module is the query the UI actually runs: "latest score per module".
Index(
    "ix_analysis_results_case_module_created",
    AnalysisResult.case_id,
    AnalysisResult.module,
    AnalysisResult.created_at.desc(),
)

# Worker claims by (status, created_at) when draining a queue.
Index("ix_analysis_jobs_status_created", AnalysisJob.status, AnalysisJob.created_at)
