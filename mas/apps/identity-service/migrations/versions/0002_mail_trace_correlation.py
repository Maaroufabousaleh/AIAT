"""Add safe trace correlation to outbound mail delivery attempts."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_mail_trace_correlation"
down_revision = "0001_identity_control_plane"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("outbound_delivery_attempts", sa.Column("trace_id", sa.Text()))
    op.add_column("outbound_delivery_attempts", sa.Column("span_id", sa.Text()))
    op.create_index(
        "ix_outbound_delivery_attempt_trace_id",
        "outbound_delivery_attempts",
        ["trace_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_outbound_delivery_attempt_trace_id",
        table_name="outbound_delivery_attempts",
    )
    op.drop_column("outbound_delivery_attempts", "span_id")
    op.drop_column("outbound_delivery_attempts", "trace_id")
