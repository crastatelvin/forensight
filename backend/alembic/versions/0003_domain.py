"""core domain: cases, evidence, custody, findings, entities, timeline, audit

Revision ID: 0003_domain
Revises: 0002_auth
Create Date: 2026-09-29 00:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_domain"
down_revision: str | None = "0002_auth"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)
_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "cases",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("number", sa.String(64), nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(32), nullable=False, server_default="intake"),
        sa.Column("owner_id", _UUID, nullable=False),
        sa.Column("created_at", _TS, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", _TS, server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_cases_number", "cases", ["number"], unique=True)
    op.create_index("ix_cases_owner_id", "cases", ["owner_id"])

    op.create_table(
        "evidence",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column(
            "case_id", _UUID,
            sa.ForeignKey("cases.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("filename", sa.String(512), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("content_type", sa.String(255), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("storage_key", sa.String(1024), nullable=False),
        sa.Column("uploaded_by", _UUID, nullable=False),
        sa.Column("created_at", _TS, server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_evidence_case_id", "evidence", ["case_id"])
    op.create_index("ix_evidence_sha256", "evidence", ["sha256"])

    op.create_table(
        "evidence_versions",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column(
            "evidence_id", _UUID,
            sa.ForeignKey("evidence.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", _TS, server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_evidence_versions_evidence_id", "evidence_versions", ["evidence_id"]
    )

    op.create_table(
        "chain_of_custody",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column(
            "evidence_id", _UUID,
            sa.ForeignKey("evidence.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("actor_id", _UUID, nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", _TS, server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_chain_of_custody_evidence_id", "chain_of_custody", ["evidence_id"]
    )

    op.create_table(
        "findings",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column(
            "case_id", _UUID,
            sa.ForeignKey("cases.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("created_by", _UUID, nullable=False),
        sa.Column("created_at", _TS, server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_findings_case_id", "findings", ["case_id"])

    op.create_table(
        "entities",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column(
            "case_id", _UUID,
            sa.ForeignKey("cases.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("value", sa.String(512), nullable=False),
        sa.Column(
            "attributes", postgresql.JSONB(), nullable=False, server_default="{}"
        ),
        sa.Column("created_at", _TS, server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_entities_case_id", "entities", ["case_id"])

    op.create_table(
        "timeline_events",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column(
            "case_id", _UUID,
            sa.ForeignKey("cases.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("occurred_at", _TS, nullable=True),
        sa.Column("label", sa.String(512), nullable=False),
        sa.Column("source_ref", sa.String(512), nullable=False, server_default=""),
        sa.Column("uncertain", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", _TS, server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_timeline_events_case_id", "timeline_events", ["case_id"])

    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("actor_id", _UUID, nullable=True),
        sa.Column("action", sa.String(128), nullable=False),
        sa.Column("target", sa.String(512), nullable=False, server_default=""),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("prev_hash", sa.String(64), nullable=False),
        sa.Column("entry_hash", sa.String(64), nullable=False),
        sa.Column("created_at", _TS, server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_audit_log_entry_hash", "audit_log", ["entry_hash"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_audit_log_entry_hash", table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_index("ix_timeline_events_case_id", table_name="timeline_events")
    op.drop_table("timeline_events")
    op.drop_index("ix_entities_case_id", table_name="entities")
    op.drop_table("entities")
    op.drop_index("ix_findings_case_id", table_name="findings")
    op.drop_table("findings")
    op.drop_index("ix_chain_of_custody_evidence_id", table_name="chain_of_custody")
    op.drop_table("chain_of_custody")
    op.drop_index("ix_evidence_versions_evidence_id", table_name="evidence_versions")
    op.drop_table("evidence_versions")
    op.drop_index("ix_evidence_sha256", table_name="evidence")
    op.drop_index("ix_evidence_case_id", table_name="evidence")
    op.drop_table("evidence")
    op.drop_index("ix_cases_owner_id", table_name="cases")
    op.drop_index("ix_cases_number", table_name="cases")
    op.drop_table("cases")

