"""Persist subordinate runtime references for restart reconciliation."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0044_worker_run_runtime_bindings"
down_revision = "0043_project_transition_outbox"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "worker_run_runtime_bindings",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "run_id",
            sa.UUID(),
            sa.ForeignKey("worker_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column(
            "adapter_id",
            sa.UUID(),
            sa.ForeignKey("runtime_adapters.id", ondelete="SET NULL"),
        ),
        sa.Column("runtime_type", sa.Text(), nullable=False, server_default="unknown"),
        sa.Column("runtime_run_id", sa.Text()),
        sa.Column("status", sa.Text(), nullable=False, server_default="ACCEPTED"),
        sa.Column("metadata", JSONB(), nullable=False, server_default="{}"),
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
        sa.Column("last_reconciled_at", sa.TIMESTAMP(timezone=True)),
        sa.UniqueConstraint(
            "run_id",
            "attempt_count",
            name="uq_worker_runtime_binding_attempt",
        ),
        sa.CheckConstraint(
            "status IN ('ACCEPTED', 'RUNNING', 'PAUSED', 'SUCCEEDED', 'FAILED', 'CANCELLED', 'TIMED_OUT', 'UNKNOWN')",
            name="ck_worker_runtime_binding_status",
        ),
    )
    op.create_index(
        "ix_worker_runtime_bindings_run_status",
        "worker_run_runtime_bindings",
        ["run_id", "status", "attempt_count"],
    )
    op.create_index(
        "ix_worker_runtime_bindings_runtime_id",
        "worker_run_runtime_bindings",
        ["runtime_run_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_worker_runtime_bindings_runtime_id",
        table_name="worker_run_runtime_bindings",
    )
    op.drop_index(
        "ix_worker_runtime_bindings_run_status",
        table_name="worker_run_runtime_bindings",
    )
    op.drop_table("worker_run_runtime_bindings")
