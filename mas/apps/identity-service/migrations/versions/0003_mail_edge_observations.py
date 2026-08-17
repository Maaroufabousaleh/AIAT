"""Persist normalized, payload-free provider mail-edge observations."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0003_mail_edge_observations"
down_revision = "0002_mail_trace_correlation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mail_edge_observations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("schema_version", sa.Text(), nullable=False, server_default="aiat.mail-edge-observation.v1"),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("event_id", sa.Text(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("failure_class", sa.Text()),
        # These identifiers remain opaque references so an incomplete or
        # externally-issued identifier cannot break the identity database.
        sa.Column("worker_id", sa.Text()),
        sa.Column("outbound_request_id", sa.Text()),
        sa.Column("provider_message_ref", sa.Text()),
        sa.Column("trace_id", sa.Text()),
        sa.Column("span_id", sa.Text()),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("signature_verified", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("metadata_json", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("provider", "event_id", name="uq_mail_edge_provider_event"),
        sa.CheckConstraint(
            "source IN ('delivery_attempt', 'provider_webhook', 'provider_poll')",
            name="ck_mail_edge_source",
        ),
        sa.CheckConstraint(
            "outcome IN ('success', 'failure', 'unknown')",
            name="ck_mail_edge_outcome",
        ),
    )
    op.create_index(
        "ix_mail_edge_observations_trace_id",
        "mail_edge_observations",
        ["trace_id"],
    )
    op.create_index(
        "ix_mail_edge_observations_worker_id",
        "mail_edge_observations",
        ["worker_id"],
    )
    op.create_index(
        "ix_mail_edge_observations_provider_message_ref",
        "mail_edge_observations",
        ["provider_message_ref"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_mail_edge_observations_provider_message_ref",
        table_name="mail_edge_observations",
    )
    op.drop_index(
        "ix_mail_edge_observations_worker_id",
        table_name="mail_edge_observations",
    )
    op.drop_index(
        "ix_mail_edge_observations_trace_id",
        table_name="mail_edge_observations",
    )
    op.drop_table("mail_edge_observations")
