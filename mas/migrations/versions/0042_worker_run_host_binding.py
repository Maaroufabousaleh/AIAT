"""Bind durable worker runs to AIAT-owned worker-host reservations."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0042_worker_run_host_binding"
down_revision = "0041_worker_host_planes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "worker_run_host_bindings",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "run_id",
            sa.UUID(),
            sa.ForeignKey("worker_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "worker_id",
            sa.UUID(),
            sa.ForeignKey("worker_registry.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "host_id",
            sa.UUID(),
            sa.ForeignKey("worker_hosts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "reservation_id",
            sa.UUID(),
            sa.ForeignKey("worker_host_reservations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("host_lease_generation", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("assignment_key", sa.Text(), nullable=False),
        sa.Column("owner", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False, server_default="ASSIGNED"),
        sa.Column("metadata", JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("committed_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("released_at", sa.TIMESTAMP(timezone=True)),
        sa.UniqueConstraint("run_id", name="uq_worker_run_host_binding_run"),
        sa.UniqueConstraint("assignment_key", name="uq_worker_run_host_binding_key"),
        sa.UniqueConstraint("reservation_id", name="uq_worker_run_host_binding_reservation"),
        sa.CheckConstraint(
            "state IN ('ASSIGNED', 'COMMITTED', 'RELEASED')",
            name="ck_worker_run_host_binding_state",
        ),
    )
    op.create_index(
        "ix_worker_run_host_bindings_worker_state",
        "worker_run_host_bindings",
        ["worker_id", "state"],
    )
    op.create_index(
        "ix_worker_run_host_bindings_host_state",
        "worker_run_host_bindings",
        ["host_id", "state"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_worker_run_host_bindings_host_state",
        table_name="worker_run_host_bindings",
    )
    op.drop_index(
        "ix_worker_run_host_bindings_worker_state",
        table_name="worker_run_host_bindings",
    )
    op.drop_table("worker_run_host_bindings")
