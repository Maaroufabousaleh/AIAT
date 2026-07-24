"""Persist browser identities and signed tool-request replay nonces.

Revision ID: 0023_durable_browser_identity_and_tool_nonces
Revises: 0022_credential_policy_enforcement
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0023_durable_browser_identity_and_tool_nonces"
down_revision = "0022_credential_policy_enforcement"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "worker_browser_identities",
        sa.Column("worker_id", sa.Text(), primary_key=True),
        sa.Column("namespace_ref", sa.Text(), nullable=False, unique=True),
        sa.Column("state", sa.Text(), nullable=False, server_default="ACTIVE"),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "state IN ('ACTIVE', 'SUSPENDED', 'RETIRED')",
            name="ck_worker_browser_identity_state",
        ),
    )
    op.create_table(
        "tool_signature_nonces",
        sa.Column("client_id", sa.Text(), nullable=False),
        sa.Column("nonce", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "client_id", "nonce", name="pk_tool_signature_nonces"
        ),
    )
    op.create_index(
        "ix_tool_signature_nonces_expiry", "tool_signature_nonces", ["expires_at"]
    )


def downgrade() -> None:
    op.drop_table("tool_signature_nonces")
    op.drop_table("worker_browser_identities")
