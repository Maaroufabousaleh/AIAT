"""Persist WorkerRun retry lineage and dispatch policy context."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0048_worker_run_retry_lineage"
down_revision = "0047_flow_approval_execution_binding"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "worker_runs",
        sa.Column(
            "retry_of_run_id",
            sa.UUID(),
            sa.ForeignKey("worker_runs.id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )
    op.add_column(
        "worker_runs",
        sa.Column(
            "retry_context_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_worker_runs_retry_of_run_id",
        "worker_runs",
        ["retry_of_run_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_worker_runs_retry_of_run_id", table_name="worker_runs")
    op.drop_column("worker_runs", "retry_context_json")
    op.drop_column("worker_runs", "retry_of_run_id")
