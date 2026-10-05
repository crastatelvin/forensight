"""analysis jobs + results (Phase 4)

Revision ID: 0005_analysis
Revises: 0004_case_workflow
Create Date: 2026-10-06 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_analysis"
down_revision: str | None = "0004_case_workflow"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)
_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "analysis_jobs",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column(
            "case_id",
            _UUID,
            sa.ForeignKey("cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "evidence_id",
            _UUID,
            sa.ForeignKey("evidence.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("module", sa.String(64), nullable=False),
        sa.Column("model_version", sa.String(64), nullable=False),
        # UNIQUE: this is what collapses concurrent identical submissions to one
        # job. A read-then-write check would let two racers both insert.
        sa.Column("idempotency_key", sa.String(64), nullable=False, unique=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column(
            "params",
            postgresql.JSONB().with_variant(sa.JSON(), "sqlite"),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
        sa.Column("unsupported_reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("cached", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("requested_by", _UUID, nullable=False),
        sa.Column("created_at", _TS, server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", _TS, nullable=True),
        sa.Column("finished_at", _TS, nullable=True),
    )
    op.create_index("ix_analysis_jobs_case_id", "analysis_jobs", ["case_id"])
    op.create_index("ix_analysis_jobs_evidence_id", "analysis_jobs", ["evidence_id"])
    op.create_index("ix_analysis_jobs_status_created", "analysis_jobs", ["status", "created_at"])

    op.create_table(
        "analysis_results",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column(
            "job_id",
            _UUID,
            sa.ForeignKey("analysis_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "case_id",
            _UUID,
            sa.ForeignKey("cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "evidence_id",
            _UUID,
            sa.ForeignKey("evidence.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("module", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("threshold", sa.Float(), nullable=False),
        sa.Column("model_version", sa.String(64), nullable=False),
        sa.Column("tier", sa.String(1), nullable=False),
        sa.Column("disclaimer", sa.Text(), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB().with_variant(sa.JSON(), "sqlite"),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("created_at", _TS, server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_analysis_results_job_id", "analysis_results", ["job_id"])
    op.create_index("ix_analysis_results_case_id", "analysis_results", ["case_id"])
    op.create_index("ix_analysis_results_evidence_id", "analysis_results", ["evidence_id"])
    op.create_index("ix_analysis_results_idempotency_key", "analysis_results", ["idempotency_key"])
    op.create_index(
        "ix_analysis_results_case_module_created",
        "analysis_results",
        ["case_id", "module", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_analysis_results_case_module_created", table_name="analysis_results")
    op.drop_index("ix_analysis_results_idempotency_key", table_name="analysis_results")
    op.drop_index("ix_analysis_results_evidence_id", table_name="analysis_results")
    op.drop_index("ix_analysis_results_case_id", table_name="analysis_results")
    op.drop_index("ix_analysis_results_job_id", table_name="analysis_results")
    op.drop_table("analysis_results")
    op.drop_index("ix_analysis_jobs_status_created", table_name="analysis_jobs")
    op.drop_index("ix_analysis_jobs_evidence_id", table_name="analysis_jobs")
    op.drop_index("ix_analysis_jobs_case_id", table_name="analysis_jobs")
    op.drop_table("analysis_jobs")
