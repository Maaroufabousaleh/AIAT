"""Add immutable transition history for worker, steward, and rollout state.

Revision ID: 0016_worker_governance_transition_history
Revises: 0015_worker_governance
Create Date: 2026-07-20

The initial worker-governance revision has already been applied in active
development stacks.  Keep its shape immutable and add audit-transition
tables in this successor revision so both fresh and upgraded installations
receive the same schema.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0016_worker_transition_history"
down_revision = "0015_worker_governance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "steward_transitions",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("steward_id", sa.UUID(), sa.ForeignKey("steward_agents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("from_status", sa.Text(), nullable=False),
        sa.Column("to_status", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text()),
        sa.Column("correlation_id", sa.Text()),
        sa.Column("evidence", JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_table(
        "rollout_transitions",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("rollout_id", sa.UUID(), sa.ForeignKey("rollout_records.id", ondelete="CASCADE"), nullable=False),
        sa.Column("from_status", sa.Text(), nullable=False),
        sa.Column("to_status", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text()),
        sa.Column("evidence", JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_table(
        "worker_run_transitions",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("run_id", sa.UUID(), sa.ForeignKey("worker_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("from_state", sa.Text(), nullable=False),
        sa.Column("to_state", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text()),
        sa.Column("correlation_id", sa.Text()),
        sa.Column("metadata", JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("idx_steward_transitions_steward_time", "steward_transitions", ["steward_id", "created_at"])
    op.create_index("idx_rollout_transitions_rollout_time", "rollout_transitions", ["rollout_id", "created_at"])
    op.create_index("idx_worker_run_transitions_run_time", "worker_run_transitions", ["run_id", "created_at"])


def downgrade() -> None:
    op.drop_index("idx_worker_run_transitions_run_time", table_name="worker_run_transitions")
    op.drop_index("idx_rollout_transitions_rollout_time", table_name="rollout_transitions")
    op.drop_index("idx_steward_transitions_steward_time", table_name="steward_transitions")
    op.drop_table("worker_run_transitions")
    op.drop_table("rollout_transitions")
    op.drop_table("steward_transitions")
