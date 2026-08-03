"""Add durable external actor mappings and bounded inbound canary plans."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB


revision = "0027_pm_actor_mappings_inbound_canary"
down_revision = "0026_pm_lifecycle_transition_plans"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pm_external_actor_mappings",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("connection_id", sa.UUID(), sa.ForeignKey("pm_connections.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider_kind", sa.Text(), nullable=False),
        sa.Column("tenant_key", sa.Text(), nullable=False),
        sa.Column("external_actor_id", sa.Text(), nullable=False),
        sa.Column("actor_snapshot", JSONB(), nullable=False, server_default="{}"),
        sa.Column("aiat_identity_id", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="TRUSTED"),
        sa.Column("authorized_scopes", JSONB(), nullable=False, server_default="[]"),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("approved_by", sa.Text(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("approved_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("revoked_by", sa.Text()),
        sa.Column("revoked_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("revocation_reason", sa.Text()),
        sa.Column("revision", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("connection_id", "tenant_key", "external_actor_id", name="uq_pm_external_actor_connection"),
    )
    op.create_index("ix_pm_external_actor_lookup", "pm_external_actor_mappings", ["connection_id", "external_actor_id", "status"])
    op.create_table(
        "pm_external_actor_mapping_audits",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("mapping_id", sa.UUID(), sa.ForeignKey("pm_external_actor_mappings.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("before_state", JSONB(), nullable=False, server_default="{}"),
        sa.Column("after_state", JSONB(), nullable=False, server_default="{}"),
        sa.Column("evidence_refs", JSONB(), nullable=False, server_default="{}"),
        sa.Column("occurred_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_pm_external_actor_audit_mapping", "pm_external_actor_mapping_audits", ["mapping_id", "occurred_at"])
    op.create_table(
        "pm_inbound_canary_plans",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("connection_id", sa.UUID(), sa.ForeignKey("pm_connections.id", ondelete="CASCADE"), nullable=False),
        sa.Column("binding_id", sa.UUID(), sa.ForeignKey("pm_project_bindings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.UUID(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("canonical_issue_id", sa.UUID(), sa.ForeignKey("issues.id", ondelete="CASCADE"), nullable=False),
        sa.Column("external_issue_id", sa.Text(), nullable=False),
        sa.Column("mapping_id", sa.UUID(), sa.ForeignKey("pm_object_mappings.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("actor_mapping_id", sa.UUID(), sa.ForeignKey("pm_external_actor_mappings.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("expected_connection_status", sa.Text(), nullable=False),
        sa.Column("expected_binding_status", sa.Text(), nullable=False),
        sa.Column("expected_connection_revision", sa.BigInteger(), nullable=False),
        sa.Column("expected_binding_revision", sa.BigInteger(), nullable=False),
        sa.Column("expected_canonical_revision", sa.BigInteger(), nullable=False),
        sa.Column("current_priority", sa.Text(), nullable=False),
        sa.Column("target_priority", sa.Text(), nullable=False),
        sa.Column("max_command_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("accepted_command_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("operations", JSONB(), nullable=False, server_default="[]"),
        sa.Column("gate_results", JSONB(), nullable=False, server_default="{}"),
        sa.Column("evidence_refs", JSONB(), nullable=False, server_default="{}"),
        sa.Column("rollback_operations", JSONB(), nullable=False, server_default="[]"),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("digest", sa.Text(), nullable=False, unique=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="PLANNED"),
        sa.Column("approved_by", sa.Text()),
        sa.Column("approved_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("armed_by", sa.Text()),
        sa.Column("armed_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("result", JSONB()),
        sa.Column("error", sa.Text()),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_pm_inbound_canary_binding_status", "pm_inbound_canary_plans", ["binding_id", "status", "expires_at"])


def downgrade() -> None:
    op.drop_index("ix_pm_inbound_canary_binding_status", table_name="pm_inbound_canary_plans")
    op.drop_table("pm_inbound_canary_plans")
    op.drop_index("ix_pm_external_actor_audit_mapping", table_name="pm_external_actor_mapping_audits")
    op.drop_table("pm_external_actor_mapping_audits")
    op.drop_index("ix_pm_external_actor_lookup", table_name="pm_external_actor_mappings")
    op.drop_table("pm_external_actor_mappings")
