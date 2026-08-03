"""Persist digest-bound PM lifecycle transition plans and audit history."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0026_pm_lifecycle_transition_plans"
down_revision = "0025_pm_project_provisioning_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "pm_connections",
        sa.Column("revision", sa.BigInteger(), nullable=False, server_default="1"),
    )
    op.add_column(
        "pm_project_bindings",
        sa.Column("revision", sa.BigInteger(), nullable=False, server_default="1"),
    )
    op.create_table(
        "pm_lifecycle_plans",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("plan_kind", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("target_type", sa.Text(), nullable=False),
        sa.Column("target_id", sa.UUID(), nullable=False),
        sa.Column("connection_id", sa.UUID(), sa.ForeignKey("pm_connections.id", ondelete="CASCADE"), nullable=False),
        sa.Column("binding_id", sa.UUID(), sa.ForeignKey("pm_project_bindings.id", ondelete="CASCADE")),
        sa.Column("expected_connection_status", sa.Text()),
        sa.Column("expected_binding_status", sa.Text()),
        sa.Column("expected_connection_revision", sa.BigInteger()),
        sa.Column("expected_binding_revision", sa.BigInteger()),
        sa.Column("desired_connection_status", sa.Text()),
        sa.Column("desired_binding_status", sa.Text()),
        sa.Column("observed_versions", JSONB(), nullable=False, server_default="{}"),
        sa.Column("operations", JSONB(), nullable=False, server_default="[]"),
        sa.Column("gate_results", JSONB(), nullable=False, server_default="{}"),
        sa.Column("evidence_refs", JSONB(), nullable=False, server_default="{}"),
        sa.Column("blockers", JSONB(), nullable=False, server_default="[]"),
        sa.Column("rollback_operations", JSONB(), nullable=False, server_default="[]"),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("digest", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="PLANNED"),
        sa.Column("approval_actor", sa.Text()),
        sa.Column("approved_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("approval_reason", sa.Text()),
        sa.Column("applied_actor", sa.Text()),
        sa.Column("applied_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("application_result", JSONB()),
        sa.Column("error", sa.Text()),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("digest", name="uq_pm_lifecycle_plan_digest"),
    )
    op.create_index(
        "ix_pm_lifecycle_plans_target",
        "pm_lifecycle_plans",
        ["target_type", "target_id", "created_at"],
    )
    op.create_index(
        "ix_pm_lifecycle_plans_status",
        "pm_lifecycle_plans",
        ["status", "expires_at"],
    )
    op.create_table(
        "pm_lifecycle_audits",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("plan_id", sa.UUID(), sa.ForeignKey("pm_lifecycle_plans.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("connection_id", sa.UUID(), sa.ForeignKey("pm_connections.id", ondelete="CASCADE"), nullable=False),
        sa.Column("binding_id", sa.UUID(), sa.ForeignKey("pm_project_bindings.id", ondelete="SET NULL")),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("before_state", JSONB(), nullable=False),
        sa.Column("after_state", JSONB(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("approval_reference", JSONB(), nullable=False, server_default="{}"),
        sa.Column("evidence_refs", JSONB(), nullable=False, server_default="{}"),
        sa.Column("transaction_id", sa.Text(), nullable=False),
        sa.Column("rollback_operations", JSONB(), nullable=False, server_default="[]"),
        sa.Column("occurred_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index(
        "ix_pm_lifecycle_audits_plan",
        "pm_lifecycle_audits",
        ["plan_id", "occurred_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_pm_lifecycle_audits_plan", table_name="pm_lifecycle_audits")
    op.drop_table("pm_lifecycle_audits")
    op.drop_index("ix_pm_lifecycle_plans_status", table_name="pm_lifecycle_plans")
    op.drop_index("ix_pm_lifecycle_plans_target", table_name="pm_lifecycle_plans")
    op.drop_table("pm_lifecycle_plans")
    op.drop_column("pm_project_bindings", "revision")
    op.drop_column("pm_connections", "revision")
