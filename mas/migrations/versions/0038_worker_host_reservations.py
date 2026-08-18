"""Add durable, idempotent worker-host capacity reservations."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0038_worker_host_reservations"
down_revision = "0037_worker_host_registry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "worker_host_reservations",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "host_id",
            sa.UUID(),
            sa.ForeignKey("worker_hosts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("reservation_key", sa.Text(), nullable=False),
        sa.Column("owner", sa.Text(), nullable=False),
        sa.Column("resource_json", JSONB(), nullable=False, server_default="{}"),
        sa.Column("state", sa.Text(), nullable=False, server_default="RESERVED"),
        sa.Column("lease_expires_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("metadata", JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("committed_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("released_at", sa.TIMESTAMP(timezone=True)),
        sa.UniqueConstraint("reservation_key", name="uq_worker_host_reservation_key"),
        sa.CheckConstraint(
            "state IN ('RESERVED', 'COMMITTED', 'RELEASED', 'EXPIRED')",
            name="ck_worker_host_reservation_state",
        ),
    )
    op.create_index(
        "ix_worker_host_reservations_host_state",
        "worker_host_reservations",
        ["host_id", "state"],
    )
    op.create_index(
        "ix_worker_host_reservations_lease",
        "worker_host_reservations",
        ["state", "lease_expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_worker_host_reservations_lease", table_name="worker_host_reservations")
    op.drop_index("ix_worker_host_reservations_host_state", table_name="worker_host_reservations")
    op.drop_table("worker_host_reservations")
