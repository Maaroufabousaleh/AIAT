"""Add durable laptop identity-event cursors and worker lifecycle mirrors.

Revision ID: 0020_identity_laptop_reconciliation
Revises: 0019_block_universal_contract
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0020_identity_laptop_reconciliation"
down_revision = "0019_block_universal_contract"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Alembic creates ``alembic_version.version_num`` as VARCHAR(32), while
    # this and later descriptive revision identifiers are longer. Widen it
    # before Alembic records this revision at the end of the transaction.
    op.alter_column(
        "alembic_version",
        "version_num",
        existing_type=sa.String(length=32),
        type_=sa.String(length=128),
        existing_nullable=False,
    )
    op.create_table(
        "identity_reconciliation_cursors",
        sa.Column("client_id", sa.Text(), primary_key=True),
        sa.Column("last_sequence", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_table(
        "worker_identity_lifecycle",
        sa.Column("worker_id", sa.UUID(), sa.ForeignKey("worker_registry.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("identity_address", sa.Text()),
        sa.Column("identity_service_id", sa.UUID()),
        sa.Column("provisioning_key", sa.Text(), unique=True),
        sa.Column("last_event_sequence", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("failure_code", sa.Text()),
        sa.Column("evidence_json", sa.dialects.postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_worker_identity_lifecycle_state", "worker_identity_lifecycle", ["state"])


def downgrade() -> None:
    op.drop_table("worker_identity_lifecycle")
    op.drop_table("identity_reconciliation_cursors")
