"""Add provider-neutral PM/SCM integration control-plane persistence.

Canonical project, sprint, and issue state remains owned by the orchestrator.
The new tables contain only connection, mapping, inbox, outbox, delivery, and
conflict state needed to project that canonical state to external providers.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0024_pm_provider_control_plane"
down_revision = "0023_durable_browser_identity_and_tool_nonces"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("projects", "sprints", "issues"):
        op.add_column(table, sa.Column("revision", sa.BigInteger(), nullable=False, server_default="1"))
    op.add_column(
        "sprints",
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.add_column(
        "issues",
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    op.create_table(
        "pm_connections",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("provider_kind", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=False),
        sa.Column("credential_ref", sa.Text(), nullable=False),
        sa.Column("capability_profile", sa.Text(), nullable=False, server_default="pm"),
        sa.Column("config", JSONB(), nullable=False, server_default="{}"),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.Text(), nullable=False, server_default="DISABLED"),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("last_health_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("last_health_status", sa.Text()),
        sa.Column("last_health_error", sa.Text()),
    )
    op.create_table(
        "pm_project_bindings",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("project_id", sa.UUID(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("connection_id", sa.UUID(), sa.ForeignKey("pm_connections.id", ondelete="CASCADE"), nullable=False),
        sa.Column("external_project_id", sa.Text()),
        sa.Column("external_repository", sa.Text()),
        sa.Column("mapping_profile", sa.Text(), nullable=False, server_default="default"),
        sa.Column("direction", sa.Text(), nullable=False, server_default="outbound"),
        sa.Column("sync_cursor", sa.Text()),
        sa.Column("status", sa.Text(), nullable=False, server_default="DISABLED"),
        sa.Column("last_reconciled_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_pm_project_bindings_project", "pm_project_bindings", ["project_id"])
    op.create_index("ix_pm_project_bindings_active_inbound", "pm_project_bindings", ["project_id", "status", "direction"])
    op.create_index(
        "uq_pm_one_active_inbound_binding",
        "pm_project_bindings",
        ["project_id"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE' AND direction IN ('inbound', 'both')"),
    )
    op.create_table(
        "pm_object_mappings",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("connection_id", sa.UUID(), sa.ForeignKey("pm_connections.id", ondelete="CASCADE"), nullable=False),
        sa.Column("object_type", sa.Text(), nullable=False),
        sa.Column("aiat_object_id", sa.UUID(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("external_key", sa.Text()),
        sa.Column("provider_version", sa.Text()),
        sa.Column("content_hash", sa.Text()),
        sa.Column("last_import_revision", sa.BigInteger()),
        sa.Column("last_export_revision", sa.BigInteger()),
        sa.Column("last_imported_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("last_exported_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("metadata", JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("connection_id", "object_type", "aiat_object_id", name="uq_pm_mapping_aiat"),
        sa.UniqueConstraint("connection_id", "object_type", "external_id", name="uq_pm_mapping_external"),
    )
    op.create_table(
        "pm_inbox_events",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("connection_id", sa.UUID(), sa.ForeignKey("pm_connections.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider_delivery_id", sa.Text(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("raw_body", sa.LargeBinary()),
        sa.Column("headers", JSONB(), nullable=False, server_default="{}"),
        sa.Column("payload_hash", sa.Text(), nullable=False),
        sa.Column("verified", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("status", sa.Text(), nullable=False, server_default="RECEIVED"),
        sa.Column("normalized_type", sa.Text()),
        sa.Column("result", JSONB()),
        sa.Column("error", sa.Text()),
        sa.Column("correlation_id", sa.Text()),
        sa.Column("causation_id", sa.Text()),
        sa.Column("received_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("processed_at", sa.TIMESTAMP(timezone=True)),
        sa.UniqueConstraint("connection_id", "provider_delivery_id", name="uq_pm_inbox_delivery"),
    )
    op.create_table(
        "pm_outbox_events",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("connection_id", sa.UUID(), sa.ForeignKey("pm_connections.id", ondelete="CASCADE"), nullable=False),
        sa.Column("aggregate_type", sa.Text(), nullable=False),
        sa.Column("aggregate_id", sa.UUID(), nullable=False),
        sa.Column("canonical_revision", sa.BigInteger(), nullable=False),
        sa.Column("operation", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False, unique=True),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="PENDING"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("claimed_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("next_attempt_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("last_error", sa.Text()),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("processed_at", sa.TIMESTAMP(timezone=True)),
    )
    op.create_index("ix_pm_outbox_pending", "pm_outbox_events", ["status", "next_attempt_at"])
    op.create_index("ix_pm_outbox_processing_lease", "pm_outbox_events", ["status", "claimed_at"])
    op.create_table(
        "pm_delivery_attempts",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("outbox_id", sa.UUID(), sa.ForeignKey("pm_outbox_events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("provider_status", sa.Integer()),
        sa.Column("response_metadata", JSONB(), nullable=False, server_default="{}"),
        sa.Column("error", sa.Text()),
        sa.Column("attempted_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_table(
        "pm_conflicts",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("connection_id", sa.UUID(), sa.ForeignKey("pm_connections.id", ondelete="CASCADE"), nullable=False),
        sa.Column("binding_id", sa.UUID(), sa.ForeignKey("pm_project_bindings.id", ondelete="CASCADE")),
        sa.Column("object_type", sa.Text(), nullable=False),
        sa.Column("aiat_object_id", sa.UUID()),
        sa.Column("external_id", sa.Text()),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("canonical_snapshot", JSONB()),
        sa.Column("external_snapshot", JSONB()),
        sa.Column("status", sa.Text(), nullable=False, server_default="OPEN"),
        sa.Column("resolution", JSONB()),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("resolved_at", sa.TIMESTAMP(timezone=True)),
    )
    op.create_table(
        "work_item_comments",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("issue_id", sa.UUID(), sa.ForeignKey("issues.id", ondelete="CASCADE"), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("actor_id", sa.Text(), nullable=False),
        sa.Column("run_id", sa.UUID()),
        sa.Column("approval_id", sa.UUID()),
        sa.Column("evidence_id", sa.Text()),
        sa.Column("body_blob_ref", sa.Text()),
        sa.Column("origin", sa.Text(), nullable=False, server_default="aiat"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_table(
        "work_item_links",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("issue_id", sa.UUID(), sa.ForeignKey("issues.id", ondelete="CASCADE"), nullable=False),
        sa.Column("link_type", sa.Text(), nullable=False),
        sa.Column("target_type", sa.Text(), nullable=False),
        sa.Column("target_id", sa.Text(), nullable=False),
        sa.Column("metadata", JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("issue_id", "link_type", "target_type", "target_id", name="uq_work_item_link"),
    )
    op.create_table(
        "integration_evidence_records",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("connection_id", sa.UUID(), sa.ForeignKey("pm_connections.id", ondelete="CASCADE"), nullable=False),
        sa.Column("binding_id", sa.UUID(), sa.ForeignKey("pm_project_bindings.id", ondelete="SET NULL")),
        sa.Column("project_id", sa.UUID(), sa.ForeignKey("projects.id", ondelete="SET NULL")),
        sa.Column("evidence_type", sa.Text(), nullable=False),
        sa.Column("external_id", sa.Text()),
        sa.Column("repository", sa.Text()),
        sa.Column("payload", JSONB(), nullable=False, server_default="{}"),
        sa.Column("idempotency_key", sa.Text(), nullable=False, unique=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_integration_evidence_connection_created", "integration_evidence_records", ["connection_id", "created_at"])
    op.create_table(
        "pm_reconciliation_runs",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("connection_id", sa.UUID(), sa.ForeignKey("pm_connections.id", ondelete="CASCADE"), nullable=False),
        sa.Column("binding_id", sa.UUID(), sa.ForeignKey("pm_project_bindings.id", ondelete="SET NULL")),
        sa.Column("mode", sa.Text(), nullable=False, server_default="audit"),
        sa.Column("status", sa.Text(), nullable=False, server_default="RUNNING"),
        sa.Column("cursor", sa.Text()),
        sa.Column("next_cursor", sa.Text()),
        sa.Column("counts", JSONB(), nullable=False, server_default="{}"),
        sa.Column("error", sa.Text()),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True)),
    )
    op.create_index("ix_pm_reconciliation_runs_connection", "pm_reconciliation_runs", ["connection_id", "started_at"])
    op.create_table(
        "pm_cutovers",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("project_id", sa.UUID(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("from_binding_id", sa.UUID(), sa.ForeignKey("pm_project_bindings.id", ondelete="SET NULL")),
        sa.Column("to_binding_id", sa.UUID(), sa.ForeignKey("pm_project_bindings.id", ondelete="SET NULL"), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="RUNNING"),
        sa.Column("confirmation", JSONB(), nullable=False, server_default="{}"),
        sa.Column("rollback_ready", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True)),
    )
    op.create_index("ix_pm_cutovers_project", "pm_cutovers", ["project_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_pm_cutovers_project", table_name="pm_cutovers")
    op.drop_table("pm_cutovers")
    op.drop_index("ix_integration_evidence_connection_created", table_name="integration_evidence_records")
    op.drop_table("integration_evidence_records")
    op.drop_index("ix_pm_reconciliation_runs_connection", table_name="pm_reconciliation_runs")
    op.drop_table("pm_reconciliation_runs")
    op.drop_table("work_item_links")
    op.drop_table("work_item_comments")
    op.drop_table("pm_conflicts")
    op.drop_table("pm_delivery_attempts")
    op.drop_index("ix_pm_outbox_pending", table_name="pm_outbox_events")
    op.drop_index("ix_pm_outbox_processing_lease", table_name="pm_outbox_events")
    op.drop_table("pm_outbox_events")
    op.drop_table("pm_inbox_events")
    op.drop_table("pm_object_mappings")
    op.drop_index("ix_pm_project_bindings_active_inbound", table_name="pm_project_bindings")
    op.drop_index("uq_pm_one_active_inbound_binding", table_name="pm_project_bindings")
    op.drop_index("ix_pm_project_bindings_project", table_name="pm_project_bindings")
    op.drop_table("pm_project_bindings")
    op.drop_table("pm_connections")
    op.drop_column("issues", "updated_at")
    op.drop_column("sprints", "updated_at")
    for table in ("issues", "sprints", "projects"):
        op.drop_column(table, "revision")
