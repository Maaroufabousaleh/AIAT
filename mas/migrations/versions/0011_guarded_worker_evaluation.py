"""Add guarded worker evaluation fields.

Revision ID: 0011_guarded_worker_evaluation
Revises: 0010_privileged_ops
Create Date: 2026-05-18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0011_guarded_worker_evaluation"
down_revision = "0010_privileged_ops"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "evaluation_reports",
        sa.Column("risk_tier", sa.Text(), nullable=False, server_default="'unknown'"),
    )
    op.add_column(
        "evaluation_reports",
        sa.Column("blocked_reasons", JSONB(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "evaluation_reports",
        sa.Column(
            "recommended_status",
            sa.Text(),
            nullable=False,
            server_default="'PENDING_EVALUATION'",
        ),
    )
    op.add_column(
        "evaluation_reports",
        sa.Column(
            "requires_human_approval",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("evaluation_reports", "requires_human_approval")
    op.drop_column("evaluation_reports", "recommended_status")
    op.drop_column("evaluation_reports", "blocked_reasons")
    op.drop_column("evaluation_reports", "risk_tier")
