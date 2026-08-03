"""Persist the actor and timestamp for governed PM canary expiry."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0030_pm_canary_expiry_attribution"
down_revision = "0029_governed_pm_outbox_dispositions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("pm_inbound_canary_plans", sa.Column("expired_by", sa.Text()))
    op.add_column(
        "pm_inbound_canary_plans",
        sa.Column("expired_at", sa.TIMESTAMP(timezone=True)),
    )


def downgrade() -> None:
    op.drop_column("pm_inbound_canary_plans", "expired_at")
    op.drop_column("pm_inbound_canary_plans", "expired_by")
