"""Persist project-transition notification intent with canonical state."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0043_project_transition_outbox"
down_revision = "0042_worker_run_host_binding"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "project_transition_outbox",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "project_id",
            sa.UUID(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("from_state", sa.Text()),
        sa.Column("to_state", sa.Text(), nullable=False),
        sa.Column("event", sa.Text(), nullable=False),
        sa.Column("triggered_by", sa.Text()),
        sa.Column("payload", JSONB()),
        sa.Column("status", sa.Text(), nullable=False, server_default="PENDING"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("claimed_at", sa.TIMESTAMP(timezone=True)),
        sa.Column(
            "next_attempt_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_error", sa.Text()),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("published_at", sa.TIMESTAMP(timezone=True)),
        sa.CheckConstraint(
            "status IN ('PENDING', 'PROCESSING', 'PUBLISHED')",
            name="ck_project_transition_outbox_status",
        ),
    )
    op.create_index(
        "ix_project_transition_outbox_pending",
        "project_transition_outbox",
        ["status", "next_attempt_at", "created_at"],
    )
    op.create_index(
        "ix_project_transition_outbox_project_created",
        "project_transition_outbox",
        ["project_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_project_transition_outbox_project_created",
        table_name="project_transition_outbox",
    )
    op.drop_index(
        "ix_project_transition_outbox_pending",
        table_name="project_transition_outbox",
    )
    op.drop_table("project_transition_outbox")
