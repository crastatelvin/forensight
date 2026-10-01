"""assignment & sla fields on cases + findings/entities/timeline indexes

Revision ID: 0004_case_workflow
Revises: 0003_domain
Create Date: 2026-09-30 00:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_case_workflow"
down_revision: str | None = "0003_domain"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)
_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.add_column("cases", sa.Column("assigned_to", _UUID, nullable=True))
    op.add_column(
        "cases",
        sa.Column("priority", sa.String(16), nullable=False, server_default="normal"),
    )
    op.add_column("cases", sa.Column("due_at", _TS, nullable=True))
    op.create_index("ix_cases_assigned_to", "cases", ["assigned_to"])

    # Full-text search over case metadata via a generated tsvector + GIN index.
    op.execute(
        "ALTER TABLE cases ADD COLUMN search_tsv tsvector "
        "GENERATED ALWAYS AS ("
        "setweight(to_tsvector('english', coalesce(number,'')), 'A') || "
        "setweight(to_tsvector('english', coalesce(title,'')), 'A') || "
        "setweight(to_tsvector('english', coalesce(description,'')), 'B')"
        ") STORED"
    )
    op.create_index(
        "ix_cases_search_tsv", "cases", ["search_tsv"], postgresql_using="gin"
    )


def downgrade() -> None:
    op.drop_index("ix_cases_search_tsv", table_name="cases")
    op.execute("ALTER TABLE cases DROP COLUMN search_tsv")
    op.drop_index("ix_cases_assigned_to", table_name="cases")
    op.drop_column("cases", "due_at")
    op.drop_column("cases", "priority")
    op.drop_column("cases", "assigned_to")
