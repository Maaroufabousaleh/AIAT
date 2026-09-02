"""Add immutable governed dispositions for historical PM outbox failures."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB


revision = "0029_governed_pm_outbox_dispositions"
down_revision = "0028_fix_integration_evidence_constraint"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pm_outbox_dispositions",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("outbox_id", sa.UUID(), sa.ForeignKey("pm_outbox_events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("connection_id", sa.UUID(), sa.ForeignKey("pm_connections.id", ondelete="CASCADE"), nullable=False),
        sa.Column("binding_id", sa.UUID(), sa.ForeignKey("pm_project_bindings.id", ondelete="SET NULL")),
        sa.Column("disposition", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("provider_state", JSONB(), nullable=False, server_default="{}"),
        sa.Column("evidence_id", sa.UUID(), sa.ForeignKey("integration_evidence_records.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("outbox_id", name="uq_pm_outbox_disposition"),
        sa.CheckConstraint("disposition IN ('RESOLVED', 'SUPERSEDED')", name="ck_pm_outbox_disposition_kind"),
    )
    op.create_index("ix_pm_outbox_disposition_connection", "pm_outbox_dispositions", ["connection_id", "disposition"])


def downgrade() -> None:
    op.drop_index("ix_pm_outbox_disposition_connection", table_name="pm_outbox_dispositions")
    op.drop_table("pm_outbox_dispositions")
