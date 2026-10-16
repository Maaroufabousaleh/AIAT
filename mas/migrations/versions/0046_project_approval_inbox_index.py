"""Support bounded keyset pagination for the operator approval inbox."""

from __future__ import annotations

from alembic import op

revision = "0046_project_approval_inbox_index"
down_revision = "0045_worker_tool_effects"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_approval_gates_status_created_id",
        "approval_gates",
        ["status", "created_at", "id"],
    )


def downgrade() -> None:
    op.drop_index("ix_approval_gates_status_created_id", table_name="approval_gates")
