"""Enforce durable per-use credential approvals and resolve rate limits.

Revision ID: 0022_credential_policy_enforcement
Revises: 0021_durable_worker_tool_grants
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0022_credential_policy_enforcement"
down_revision = "0021_durable_worker_tool_grants"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "credential_resolve_approvals",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "secret_name",
            sa.Text(),
            sa.ForeignKey("credentials.name", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("requester", sa.Text(), nullable=False),
        sa.Column("context", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False, server_default="PENDING"),
        sa.Column("requested_by", sa.Text(), nullable=False),
        sa.Column("decided_by", sa.Text()),
        sa.Column("decision_reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("decided_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("consumed_at", sa.TIMESTAMP(timezone=True)),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "state IN ('PENDING', 'APPROVED', 'REJECTED', 'CONSUMED', 'EXPIRED')",
            name="ck_credential_resolve_approval_state",
        ),
    )
    op.create_index(
        "ix_credential_resolve_approval_lookup",
        "credential_resolve_approvals",
        ["secret_name", "requester", "context", "state", "expires_at"],
    )
    op.create_table(
        "credential_resolve_rates",
        sa.Column(
            "secret_name",
            sa.Text(),
            sa.ForeignKey("credentials.name", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("requester", sa.Text(), nullable=False),
        sa.Column("window_started_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("resolve_count", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint(
            "secret_name",
            "requester",
            "window_started_at",
            name="pk_credential_resolve_rates",
        ),
        sa.CheckConstraint(
            "resolve_count >= 0", name="ck_credential_resolve_rate_count"
        ),
    )


def downgrade() -> None:
    op.drop_table("credential_resolve_rates")
    op.drop_index(
        "ix_credential_resolve_approval_lookup",
        table_name="credential_resolve_approvals",
    )
    op.drop_table("credential_resolve_approvals")
