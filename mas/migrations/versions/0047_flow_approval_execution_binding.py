"""Bind flow approval gates to their node execution."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0047_flow_approval_execution_binding"
down_revision = "0046_project_approval_inbox_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "approval_gates",
        sa.Column(
            "flow_node_execution_id",
            sa.BigInteger(),
            sa.ForeignKey("flow_node_executions.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "uq_approval_gates_flow_node_execution_id",
        "approval_gates",
        ["flow_node_execution_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_approval_gates_flow_node_execution_id",
        table_name="approval_gates",
    )
    op.drop_column("approval_gates", "flow_node_execution_id")
