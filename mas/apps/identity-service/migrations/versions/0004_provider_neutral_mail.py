"""Add provider-neutral identity bindings and inbound synchronization state."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0004_provider_neutral_mail"
down_revision = "0003_mail_edge_observations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Keep the historical Stalwart account reference for compatibility while
    # making the selected direction providers explicit on each identity.
    op.add_column(
        "agent_email_identities",
        sa.Column("inbound_provider", sa.Text(), nullable=True, server_default="cloudflare"),
    )
    op.add_column(
        "agent_email_identities",
        sa.Column("outbound_provider", sa.Text(), nullable=True, server_default="resend"),
    )
    op.add_column(
        "outbound_mail_requests",
        sa.Column("content_type", sa.Text(), nullable=False, server_default="text/plain"),
    )
    # Every row that exists before this migration belongs to the historical
    # Stalwart topology. Preserve that provenance while making Cloudflare the
    # default for newly inserted rows that omit the optional field.
    op.execute(
        "UPDATE agent_email_identities SET inbound_provider = 'stalwart' "
        "WHERE inbound_provider = 'cloudflare'"
    )

    op.create_table(
        "email_provider_bindings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("identity_id", UUID(as_uuid=True), sa.ForeignKey("agent_email_identities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("direction", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("provider_reference", sa.Text()),
        sa.Column("lifecycle_state", sa.Text(), nullable=False),
        sa.Column("metadata_json", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("identity_id", "direction", "provider", name="uq_email_provider_binding_direction"),
        sa.CheckConstraint("direction IN ('INBOUND', 'OUTBOUND')", name="ck_email_provider_binding_direction"),
    )
    op.create_index(
        "ix_email_provider_bindings_provider_reference",
        "email_provider_bindings",
        ["provider", "provider_reference"],
    )
    op.execute(
        """INSERT INTO email_provider_bindings
             (identity_id, direction, provider, provider_reference, lifecycle_state, metadata_json)
           SELECT id, 'INBOUND', 'stalwart', provider_account_id,
                  CASE WHEN state IN ('SUSPENDED', 'ARCHIVED') THEN state ELSE 'ACTIVE' END,
                  '{}'::jsonb
             FROM agent_email_identities
            WHERE provider_account_id IS NOT NULL
           ON CONFLICT (identity_id, direction, provider) DO NOTHING"""
    )
    op.execute(
        """INSERT INTO email_provider_bindings
             (identity_id, direction, provider, provider_reference, lifecycle_state, metadata_json)
           SELECT id, 'OUTBOUND', 'resend', NULL,
                  CASE WHEN outbound_enabled THEN 'ACTIVE' ELSE 'DISABLED' END,
                  '{}'::jsonb
             FROM agent_email_identities
           ON CONFLICT (identity_id, direction, provider) DO NOTHING"""
    )

    op.create_table(
        "email_inbound_sync_cursors",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("provider", sa.Text(), nullable=False, unique=True),
        sa.Column("last_sequence", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("last_sequence >= 0", name="ck_email_inbound_cursor_nonnegative"),
    )

    # Raw MIME remains at the edge's R2 object store in the normal topology.
    # The identity service stores an encrypted, bounded read copy only when it
    # synchronizes a message; the content_ref never enters an API response.
    op.create_table(
        "email_inbound_messages",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("identity_id", UUID(as_uuid=True), sa.ForeignKey("agent_email_identities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("provider_event_id", sa.Text(), nullable=False),
        sa.Column("provider_message_id", sa.Text(), nullable=False),
        sa.Column("envelope_recipient", sa.Text(), nullable=False),
        sa.Column("sender", sa.Text()),
        sa.Column("subject", sa.Text(), nullable=False, server_default=""),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_object_ref", sa.Text(), nullable=False),
        sa.Column("content_ref", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("provider", "provider_event_id", name="uq_email_inbound_provider_event"),
        sa.UniqueConstraint("identity_id", "provider", "provider_message_id", name="uq_email_inbound_identity_message"),
    )
    op.create_index(
        "ix_email_inbound_messages_identity_received",
        "email_inbound_messages",
        ["identity_id", "received_at"],
    )

    # The compatibility view keeps old operational SQL readable without
    # maintaining two mutable provisioning-job stores.
    op.rename_table("mailbox_provisioning_jobs", "email_identity_provisioning_jobs")
    op.execute(
        "CREATE VIEW mailbox_provisioning_jobs AS "
        "SELECT * FROM email_identity_provisioning_jobs"
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS mailbox_provisioning_jobs")
    op.rename_table("email_identity_provisioning_jobs", "mailbox_provisioning_jobs")
    op.drop_index("ix_email_inbound_messages_identity_received", table_name="email_inbound_messages")
    op.drop_table("email_inbound_messages")
    op.drop_table("email_inbound_sync_cursors")
    op.drop_index("ix_email_provider_bindings_provider_reference", table_name="email_provider_bindings")
    op.drop_table("email_provider_bindings")
    op.drop_column("agent_email_identities", "outbound_provider")
    op.drop_column("agent_email_identities", "inbound_provider")
    op.drop_column("outbound_mail_requests", "content_type")
