"""Persist mediated worker-tool outcomes for safe replay."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0045_worker_tool_effects"
down_revision = "0044_worker_run_runtime_bindings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "worker_tool_effects",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "run_id",
            sa.UUID(),
            sa.ForeignKey("worker_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("request_id", sa.UUID(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("tool_name", sa.Text(), nullable=False),
        sa.Column("request_sha256", sa.String(64), nullable=False),
        sa.Column("state", sa.Text(), nullable=False, server_default="IN_FLIGHT"),
        sa.Column("response_json", JSONB()),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True)),
        sa.UniqueConstraint(
            "run_id",
            "idempotency_key",
            name="uq_worker_tool_effect_run_key",
        ),
        sa.CheckConstraint(
            "state IN ('IN_FLIGHT', 'COMPLETED', 'AMBIGUOUS')",
            name="ck_worker_tool_effect_state",
        ),
    )
    op.create_index(
        "ix_worker_tool_effects_run_state",
        "worker_tool_effects",
        ["run_id", "state"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_worker_tool_effects_run_state",
        table_name="worker_tool_effects",
    )
    op.drop_table("worker_tool_effects")
