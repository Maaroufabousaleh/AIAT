"""Add durable worker-run queue claims, leases, and heartbeats."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0032_worker_run_queue_leases"
down_revision = "0031_company_control_plane"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("worker_runs", sa.Column("queue_priority", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("worker_runs", sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("worker_runs", sa.Column("claim_owner", sa.Text()))
    op.add_column("worker_runs", sa.Column("claimed_at", sa.TIMESTAMP(timezone=True)))
    op.add_column("worker_runs", sa.Column("lease_expires_at", sa.TIMESTAMP(timezone=True)))
    op.add_column("worker_runs", sa.Column("heartbeat_at", sa.TIMESTAMP(timezone=True)))
    op.add_column("worker_runs", sa.Column("next_attempt_at", sa.TIMESTAMP(timezone=True)))
    op.add_column("worker_runs", sa.Column("cancel_requested_at", sa.TIMESTAMP(timezone=True)))
    op.add_column("worker_runs", sa.Column("recovery_reason", sa.Text()))
    op.create_index(
        "ix_worker_runs_queue_claim",
        "worker_runs",
        ["state", "next_attempt_at", "queue_priority", "created_at"],
    )
    op.create_index(
        "ix_worker_runs_lease_expiry",
        "worker_runs",
        ["state", "lease_expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_worker_runs_lease_expiry", table_name="worker_runs")
    op.drop_index("ix_worker_runs_queue_claim", table_name="worker_runs")
    for name in (
        "recovery_reason",
        "cancel_requested_at",
        "next_attempt_at",
        "heartbeat_at",
        "lease_expires_at",
        "claimed_at",
        "claim_owner",
        "attempt_count",
        "queue_priority",
    ):
        op.drop_column("worker_runs", name)
