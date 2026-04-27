"""Add privileged_ops_audit table for CEO privilege separation.

Revision ID: 0010_privileged_ops
Revises: 0009_credentials_manager
Create Date: 2026-04-26 01:00:00.000000
"""

from __future__ import annotations

from alembic import op

revision: str = "0010_privileged_ops"
down_revision = "0009_credentials_manager"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS privileged_ops_audit (
            id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            action            TEXT NOT NULL,
            actor_id          TEXT NOT NULL,
            actor_role        TEXT NOT NULL DEFAULT 'ceo',
            payload_json      JSONB NOT NULL DEFAULT '{}',
            privilege_level   TEXT NOT NULL,
            risk_level        TEXT NOT NULL,
            requires_approval BOOLEAN NOT NULL,
            decision          TEXT NOT NULL DEFAULT 'pending_approval',
            decided_by        TEXT,
            decided_at        TIMESTAMPTZ,
            reason            TEXT,
            requested_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_privops_decision ON privileged_ops_audit (decision)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_privops_actor ON privileged_ops_audit (actor_id)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_privops_time ON privileged_ops_audit (requested_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_privops_time")
    op.execute("DROP INDEX IF EXISTS idx_privops_actor")
    op.execute("DROP INDEX IF EXISTS idx_privops_decision")
    op.execute("DROP TABLE IF EXISTS privileged_ops_audit")
