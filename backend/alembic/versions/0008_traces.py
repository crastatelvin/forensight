"""Phase 10: trace spans, stored in Postgres rather than exported to a collector.

Revises: 0007_reports

The reasoning behind storing rather than exporting is in `app/tracing.py`. In short: a
trace in the database can be joined to a case, correlated with an audit entry, and
restored from the P10.B2 backup, none of which a trace shipped to a collector can do —
and it keeps the air-gapped default intact.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_traces"
down_revision: str | None = "0007_reports"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "trace_spans",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("trace_id", sa.Uuid(), nullable=False),
        # The W3C hex span id, kept alongside the UUID primary key so a span id read
        # out of a header or a log compares directly with what is stored.
        sa.Column("span_id", sa.String(length=16), nullable=False),
        sa.Column("parent_span_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        # ON DELETE CASCADE: a span is meaningless once its case is gone, and spans
        # outlive nothing on their own.
        sa.Column("case_id", sa.Uuid(), nullable=True),
        sa.Column("started_ns", sa.BigInteger(), nullable=False),
        sa.Column("ended_ns", sa.BigInteger(), nullable=False),
        sa.Column("duration_ms", sa.Float(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="ok", nullable=False),
        sa.Column("attributes", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_trace_spans_trace_id", "trace_spans", ["trace_id"])
    op.create_index("ix_trace_spans_span_id", "trace_spans", ["span_id"])
    op.create_index("ix_trace_spans_parent_span_id", "trace_spans", ["parent_span_id"])
    op.create_index("ix_trace_spans_case_id", "trace_spans", ["case_id"])
    # Reading a trace is always "every span of this trace, in order", so the composite
    # index matches the query rather than leaving the planner to combine two.
    op.create_index(
        "ix_trace_spans_trace_started", "trace_spans", ["trace_id", "started_ns"]
    )


def downgrade() -> None:
    op.drop_index("ix_trace_spans_trace_started", table_name="trace_spans")
    op.drop_index("ix_trace_spans_case_id", table_name="trace_spans")
    op.drop_index("ix_trace_spans_parent_span_id", table_name="trace_spans")
    op.drop_index("ix_trace_spans_span_id", table_name="trace_spans")
    op.drop_index("ix_trace_spans_trace_id", table_name="trace_spans")
    op.drop_table("trace_spans")