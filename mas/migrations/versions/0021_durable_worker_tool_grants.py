"""Persist explicit worker tool grants across tool-service restarts.

Revision ID: 0021_durable_worker_tool_grants
Revises: 0020_identity_laptop_reconciliation
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0021_durable_worker_tool_grants"
down_revision = "0020_identity_laptop_reconciliation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "worker_tool_grants",
        sa.Column("worker_id", sa.Text(), nullable=False),
        sa.Column("tool_name", sa.Text(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("worker_id", "tool_name", name="pk_worker_tool_grants"),
    )
    op.create_index("ix_worker_tool_grants_worker_id", "worker_tool_grants", ["worker_id"])


def downgrade() -> None:
    op.drop_table("worker_tool_grants")
