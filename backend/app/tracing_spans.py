"""Trace span storage (P10.B1).

A separate module from `app/tracing.py` so the timing/propagation logic does not import
the ORM — which also means the propagation half is testable without a database, and the
`import` inside `record` is not a cycle.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.domain.models import JSONType


class TraceSpan(Base):
    """One recorded span.

    `span_id` is the W3C 16-hex id stored as a UUID so it can be a primary key and
    carry an index, with the original hex kept as a column — a trace id or span id read
    out of a header or a log has to be comparable with what is stored here, and
    formatting a UUID back to hex to do that is how a comparison quietly starts
    disagreeing about which spans belong to one trace.
    """

    __tablename__ = "trace_spans"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    trace_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    span_id: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    parent_span_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    case_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"), nullable=True, index=True
    )
    started_ns: Mapped[int] = mapped_column(BigInteger, nullable=False)
    ended_ns: Mapped[int] = mapped_column(BigInteger, nullable=False)
    duration_ms: Mapped[float] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="ok", nullable=False)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("ix_trace_spans_trace_started", "trace_id", "started_ns"),)
