"""Persist per-canonical-project PM provisioning and activation evidence."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0025_pm_project_provisioning_lifecycle"
down_revision = "0024_pm_provider_control_plane"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "pm_project_bindings",
        sa.Column("external_project_key", sa.Text()),
    )
    op.add_column(
        "pm_project_bindings",
        sa.Column("provisioning_state", sa.Text(), nullable=False, server_default="UNPROVISIONED"),
    )
    op.add_column("pm_project_bindings", sa.Column("provisioning_plan_id", sa.UUID()))
    op.add_column("pm_project_bindings", sa.Column("provisioning_plan_digest", sa.Text()))
    op.add_column(
        "pm_project_bindings",
        sa.Column("activation_blockers", JSONB(), nullable=False, server_default="[]"),
    )
    op.add_column("pm_project_bindings", sa.Column("webhook_verified_at", sa.TIMESTAMP(timezone=True)))
    op.add_column("pm_project_bindings", sa.Column("projection_verified_at", sa.TIMESTAMP(timezone=True)))
    op.add_column("pm_project_bindings", sa.Column("reconciliation_verified_at", sa.TIMESTAMP(timezone=True)))
    op.add_column(
        "pm_project_bindings",
        sa.Column("webhook_events", JSONB(), nullable=False, server_default="[]"),
    )
    op.create_index(
        "uq_pm_dedicated_external_project",
        "pm_project_bindings",
        ["connection_id", "external_project_id"],
        unique=True,
        postgresql_where=sa.text(
            "external_project_id IS NOT NULL AND mapping_profile = 'dedicated_project'"
        ),
    )


def downgrade() -> None:
    op.drop_index("uq_pm_dedicated_external_project", table_name="pm_project_bindings")
    op.drop_column("pm_project_bindings", "webhook_events")
    op.drop_column("pm_project_bindings", "reconciliation_verified_at")
    op.drop_column("pm_project_bindings", "projection_verified_at")
    op.drop_column("pm_project_bindings", "webhook_verified_at")
    op.drop_column("pm_project_bindings", "activation_blockers")
    op.drop_column("pm_project_bindings", "provisioning_plan_digest")
    op.drop_column("pm_project_bindings", "provisioning_plan_id")
    op.drop_column("pm_project_bindings", "provisioning_state")
    op.drop_column("pm_project_bindings", "external_project_key")
