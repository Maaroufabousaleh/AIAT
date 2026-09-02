"""Add worker-host lease generations for fencing and recovery."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0039_worker_host_fencing"
down_revision = "0038_worker_host_reservations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "worker_hosts",
        sa.Column("lease_generation", sa.BigInteger(), nullable=False, server_default="1"),
    )
    op.create_check_constraint(
        "ck_worker_hosts_lease_generation_positive",
        "worker_hosts",
        "lease_generation >= 1",
    )
    op.add_column(
        "worker_host_reservations",
        sa.Column("host_lease_generation", sa.BigInteger(), nullable=False, server_default="1"),
    )
    op.create_index(
        "ix_worker_host_reservations_host_generation_state",
        "worker_host_reservations",
        ["host_id", "host_lease_generation", "state"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_worker_host_reservations_host_generation_state",
        table_name="worker_host_reservations",
    )
    op.drop_column("worker_host_reservations", "host_lease_generation")
    op.drop_constraint("ck_worker_hosts_lease_generation_positive", "worker_hosts", type_="check")
    op.drop_column("worker_hosts", "lease_generation")
