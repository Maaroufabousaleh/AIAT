"""Close pending approval gates when their project is already terminal.

Revision ID: 0013_terminal_gate_cleanup
Revises: 0012_document_lineage
Create Date: 2026-07-15
"""

from __future__ import annotations

from alembic import op

revision = "0013_terminal_gate_cleanup"
down_revision = "0012_document_lineage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Remove stale operator work from failed, completed, and archived projects."""
    op.execute(
        """
        UPDATE approval_gates AS gates
        SET status = 'CANCELLED',
            decided_by = 'migration:0013_terminal_gate_cleanup',
            justification = 'Automatically cancelled because the project was already terminal.',
            decided_at = COALESCE(gates.decided_at, now())
        FROM projects
        WHERE gates.project_id = projects.id
          AND gates.status = 'PENDING'
          AND projects.state IN ('FAILED', 'COMPLETED', 'ARCHIVED')
        """
    )


def downgrade() -> None:
    """Do not re-open decisions that were intentionally closed by the migration."""
    pass
