"""agent runs, checkpoints, tool-call audit, and scoped memory (Phase 5)

Revision ID: 0006_agent
Revises: 0005_analysis
Create Date: 2026-10-07 00:00:00.000000

The memory embedding column is upgraded from JSON to pgvector `vector(1024)`
with an HNSW index for cosine-distance recall.

1024 is the width `bge-m3` actually returns when served by Ollama — measured,
not assumed (nomic-embed-text returns 768; a widely-quoted 1536 does not match
what these servers emit). `app.agent.repository.EMBED_DIM` must stay in step
with this number, and `write_memory` validates width on insert so a mismatched
model fails loudly rather than silently degrading recall.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_agent"
down_revision: str | None = "0005_analysis"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)
_TS = sa.DateTime(timezone=True)
_JSON = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    op.create_table(
        "agent_runs",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column(
            "case_id",
            _UUID,
            sa.ForeignKey("cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("thread_id", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("step", sa.String(32), nullable=False, server_default="intake"),
        sa.Column("objective", sa.Text(), nullable=False, server_default=""),
        sa.Column("report", sa.Text(), nullable=False, server_default=""),
        sa.Column("refusal", sa.Text(), nullable=False, server_default=""),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
        sa.Column("model", sa.String(128), nullable=False, server_default=""),
        sa.Column("requested_by", _UUID, nullable=False),
        sa.Column("created_at", _TS, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", _TS, server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_agent_runs_case_id", "agent_runs", ["case_id"])
    op.create_index("ix_agent_runs_thread_id", "agent_runs", ["thread_id"])

    op.create_table(
        "agent_checkpoints",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "run_id",
            _UUID,
            sa.ForeignKey("agent_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("step", sa.String(32), nullable=False),
        sa.Column("state", _JSON, nullable=False, server_default="{}"),
        sa.Column("created_at", _TS, server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_agent_checkpoints_run_id", "agent_checkpoints", ["run_id"])
    op.create_index("ix_agent_checkpoints_run_step", "agent_checkpoints", ["run_id", "step"])

    op.create_table(
        "agent_tool_calls",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column(
            "run_id",
            _UUID,
            sa.ForeignKey("agent_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "case_id",
            _UUID,
            sa.ForeignKey("cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("call_id", sa.String(64), nullable=False),
        sa.Column("tool", sa.String(64), nullable=False),
        sa.Column("args", _JSON, nullable=False, server_default="{}"),
        sa.Column("args_hash", sa.String(64), nullable=False),
        sa.Column("result_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("ok", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
        sa.Column("approved_action_id", sa.String(64), nullable=False, server_default=""),
        sa.Column("created_at", _TS, server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_agent_tool_calls_run_id", "agent_tool_calls", ["run_id"])
    op.create_index("ix_agent_tool_calls_case_id", "agent_tool_calls", ["case_id"])
    op.create_index("ix_agent_tool_calls_tool", "agent_tool_calls", ["tool"])

    op.create_table(
        "agent_memories",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column(
            "case_id",
            _UUID,
            sa.ForeignKey("cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("actor_id", _UUID, nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        # Created as JSON then converted: SQLite has no vector type, and a
        # column added as `vector` directly would break create_all in tests.
        sa.Column("embedding", _JSON, nullable=True),
        sa.Column("dim", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "injection_findings",
            postgresql.JSONB().with_variant(sa.JSON(), "sqlite"),
            nullable=False,
            server_default="[]",
        ),
        sa.Column(
            "run_id",
            _UUID,
            sa.ForeignKey("agent_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", _TS, server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_agent_memories_case_id", "agent_memories", ["case_id"])
    op.create_index("ix_agent_memories_actor_id", "agent_memories", ["actor_id"])
    op.create_index("ix_agent_memories_kind", "agent_memories", ["kind"])
    op.create_index(
        "ix_agent_memories_scope_kind",
        "agent_memories",
        ["case_id", "actor_id", "kind", sa.text("created_at DESC")],
    )

    # Promote embeddings to pgvector for cosine-distance recall.
    #
    # No DEFAULT: pgvector rejects a zero-dimension literal, so
    # ADD COLUMN ... vector(1024) NOT NULL DEFAULT '[]'::vector fails with
    # "vector must have at least 1 dimension" and takes the whole migration with
    # it. This table is new and therefore empty here, so dropping and re-adding
    # without a default is safe.
    # Nullable: text-only memory survives an embedding-provider outage.
    op.execute("ALTER TABLE agent_memories DROP COLUMN embedding")
    op.execute("ALTER TABLE agent_memories ADD COLUMN embedding vector(1024)")
    # HNSW beats IVFFlat here: no training step, and recall stays good at the
    # small corpus sizes a single case produces.
    op.execute(
        "CREATE INDEX ix_agent_memories_embedding_hnsw ON agent_memories "
        "USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_agent_memories_embedding_hnsw")
    op.execute("ALTER TABLE agent_memories DROP COLUMN embedding")
    op.execute("ALTER TABLE agent_memories ADD COLUMN embedding JSONB")
    # Rows written while this was a vector column cannot be represented as JSON,
    # so they are cleared on the way down rather than failing the downgrade.
    op.execute("UPDATE agent_memories SET embedding = NULL")
    op.drop_index("ix_agent_memories_scope_kind", table_name="agent_memories")
    op.drop_index("ix_agent_memories_kind", table_name="agent_memories")
    op.drop_index("ix_agent_memories_actor_id", table_name="agent_memories")
    op.drop_index("ix_agent_memories_case_id", table_name="agent_memories")
    op.drop_table("agent_memories")
    op.drop_index("ix_agent_tool_calls_tool", table_name="agent_tool_calls")
    op.drop_index("ix_agent_tool_calls_case_id", table_name="agent_tool_calls")
    op.drop_index("ix_agent_tool_calls_run_id", table_name="agent_tool_calls")
    op.drop_table("agent_tool_calls")
    op.drop_index("ix_agent_checkpoints_run_step", table_name="agent_checkpoints")
    op.drop_index("ix_agent_checkpoints_run_id", table_name="agent_checkpoints")
    op.drop_table("agent_checkpoints")
    op.drop_index("ix_agent_runs_thread_id", table_name="agent_runs")
    op.drop_index("ix_agent_runs_case_id", table_name="agent_runs")
    op.drop_table("agent_runs")
