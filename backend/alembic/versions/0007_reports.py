"""Phase 8: finding citations, so a report can say what a finding rests on.

Revises: 0006_agent
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_reports"
down_revision: str | None = "0006_agent"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "finding_citations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("finding_id", sa.Uuid(), nullable=False),
        sa.Column("result_id", sa.Uuid(), nullable=False),
        sa.Column("evidence_id", sa.Uuid(), nullable=False),
        sa.Column("module", sa.String(length=64), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("threshold", sa.Float(), nullable=False),
        sa.Column("tier", sa.String(length=8), nullable=False),
        sa.Column("model_version", sa.String(length=128), nullable=False),
        sa.Column("not_applicable", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column(
            "cited_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["finding_id"], ["findings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    # Indexed for the report's appendix, which walks citations per finding.
    op.create_index("ix_finding_citations_finding_id", "finding_citations", ["finding_id"])
    op.create_index("ix_finding_citations_result_id", "finding_citations", ["result_id"])
    op.create_index("ix_finding_citations_evidence_id", "finding_citations", ["evidence_id"])


def downgrade() -> None:
    op.drop_index("ix_finding_citations_evidence_id", table_name="finding_citations")
    op.drop_index("ix_finding_citations_result_id", table_name="finding_citations")
    op.drop_index("ix_finding_citations_finding_id", table_name="finding_citations")
    op.drop_table("finding_citations")
