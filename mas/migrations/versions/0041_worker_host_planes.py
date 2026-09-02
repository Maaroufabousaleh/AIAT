"""Add explicit AIAT host-plane identity for control/data/tool separation."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0041_worker_host_planes"
down_revision = "0040_worker_run_skill_bundle_pin"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "worker_hosts",
        sa.Column("host_plane", sa.Text(), nullable=False, server_default="worker"),
    )
    op.create_check_constraint(
        "ck_worker_hosts_host_plane",
        "worker_hosts",
        "host_plane IN ('control', 'tool', 'data', 'worker')",
    )
    op.create_index(
        "ix_worker_hosts_plane_status_lease",
        "worker_hosts",
        ["host_plane", "status", "lease_expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_worker_hosts_plane_status_lease", table_name="worker_hosts")
    op.drop_constraint("ck_worker_hosts_host_plane", "worker_hosts", type_="check")
    op.drop_column("worker_hosts", "host_plane")
