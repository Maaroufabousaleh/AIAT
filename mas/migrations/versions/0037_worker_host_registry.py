"""Add durable authenticated worker-host registration and lease state."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0037_worker_host_registry"
down_revision = "0036_native_trace_spans"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "worker_hosts",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("host_id", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="REGISTERING"),
        sa.Column("auth_token_sha256", sa.String(64), nullable=False),
        sa.Column("labels", JSONB(), nullable=False, server_default="{}"),
        sa.Column("capabilities", JSONB(), nullable=False, server_default="[]"),
        sa.Column("sandbox_profile", sa.Text(), nullable=False, server_default="standard"),
        sa.Column("isolation_mode", sa.Text(), nullable=False, server_default="native"),
        sa.Column("capacity", JSONB(), nullable=False, server_default="{}"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lease_owner", sa.Text()),
        sa.Column("lease_expires_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("heartbeat_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("last_seen_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("metadata", JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("host_id", name="uq_worker_hosts_host_id"),
        sa.CheckConstraint(
            "status IN ('REGISTERING', 'READY', 'DRAINING', 'OFFLINE', 'REVOKED')",
            name="ck_worker_hosts_status",
        ),
        sa.CheckConstraint("priority >= 0", name="ck_worker_hosts_priority"),
    )
    op.create_index(
        "ix_worker_hosts_status_lease",
        "worker_hosts",
        ["status", "lease_expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_worker_hosts_status_lease", table_name="worker_hosts")
    op.drop_table("worker_hosts")
