"""Add worker integration fields and evaluation_reports table.

Revision ID: 0007_worker_integration
Revises: 0006_flow_instance_targets
Create Date: 2026-04-04 02:00:00.000000

Tables modified
--------------
1. worker_registry — add 12 new columns for versioning, upstream tracking,
   health monitoring, and adapter configuration
2. evaluation_reports — new table for storing repo evaluation results

Design notes
------------
- All new columns on worker_registry are nullable with defaults so existing
  rows are not affected
- evaluation_reports links to worker_registry via FK with ON DELETE CASCADE
- health_status defaults to 'unknown' until first heartbeat
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0007_worker_integration"
down_revision = "0006_flow_instance_targets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "worker_registry",
        sa.Column("version", sa.Text(), nullable=True),
    )
    op.add_column(
        "worker_registry",
        sa.Column("source_repo", sa.Text(), nullable=True),
    )
    op.add_column(
        "worker_registry",
        sa.Column("source_revision", sa.Text(), nullable=True),
    )
    op.add_column(
        "worker_registry",
        sa.Column("version_pin", sa.Text(), nullable=True),
    )
    op.add_column(
        "worker_registry",
        sa.Column("update_policy", sa.Text(), nullable=False, server_default="'manual'"),
    )
    op.add_column(
        "worker_registry",
        sa.Column("evaluation_status", sa.Text(), nullable=True),
    )
    op.add_column(
        "worker_registry",
        sa.Column("adapter_entrypoint", sa.Text(), nullable=False, server_default="'WorkerAgent'"),
    )
    op.add_column(
        "worker_registry",
        sa.Column("adapter_module", sa.Text(), nullable=True),
    )
    op.add_column(
        "worker_registry",
        sa.Column("wrapper_config", JSONB(), nullable=False, server_default="{}"),
    )
    op.add_column(
        "worker_registry",
        sa.Column("isolation_mode", sa.Text(), nullable=False, server_default="'native'"),
    )
    op.add_column(
        "worker_registry",
        sa.Column("last_upstream_sync", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.add_column(
        "worker_registry",
        sa.Column("upstream_commit_sha", sa.Text(), nullable=True),
    )
    op.add_column(
        "worker_registry",
        sa.Column("health_status", sa.Text(), nullable=False, server_default="unknown"),
    )
    op.add_column(
        "worker_registry",
        sa.Column("last_seen_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.add_column(
        "worker_registry",
        sa.Column("error_count", sa.Integer(), nullable=False, server_default="0"),
    )

    op.create_table(
        "evaluation_reports",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "worker_id",
            sa.UUID(),
            sa.ForeignKey("worker_registry.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "evaluated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("checks", JSONB(), nullable=False, server_default="{}"),
        sa.Column("overall_score", sa.Float(), nullable=True),
        sa.Column("verdict", sa.Text(), nullable=False, server_default="'PENDING'"),
        sa.Column("evaluator_version", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("evaluation_reports")
    op.drop_column("worker_registry", "error_count")
    op.drop_column("worker_registry", "last_seen_at")
    op.drop_column("worker_registry", "health_status")
    op.drop_column("worker_registry", "upstream_commit_sha")
    op.drop_column("worker_registry", "last_upstream_sync")
    op.drop_column("worker_registry", "isolation_mode")
    op.drop_column("worker_registry", "wrapper_config")
    op.drop_column("worker_registry", "adapter_module")
    op.drop_column("worker_registry", "adapter_entrypoint")
    op.drop_column("worker_registry", "evaluation_status")
    op.drop_column("worker_registry", "update_policy")
    op.drop_column("worker_registry", "version_pin")
    op.drop_column("worker_registry", "source_revision")
    op.drop_column("worker_registry", "source_repo")
    op.drop_column("worker_registry", "version")
