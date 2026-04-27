"""Add credentials and credentials_audit tables.

Revision ID: 0009_credentials_manager
Revises: 0008_pgvector_support
Create Date: 2026-04-26 00:00:00.000000

Tables added
-----------
1. credentials       — encrypted secret store (name → encrypted_value + policy)
2. credentials_audit — immutable audit log of every resolve attempt
"""

from __future__ import annotations

from alembic import op

revision: str = "0009_credentials_manager"
down_revision = "0008_pgvector_support"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS credentials (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name            TEXT NOT NULL UNIQUE,
            description     TEXT NOT NULL DEFAULT '',
            secret_type     TEXT NOT NULL DEFAULT 'other',
            encrypted_value TEXT NOT NULL,
            policy_json     JSONB NOT NULL DEFAULT '{}',
            usage_count     BIGINT NOT NULL DEFAULT 0,
            last_used_at    TIMESTAMPTZ,
            created_by      TEXT NOT NULL DEFAULT 'system',
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_credentials_name ON credentials (name)
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS credentials_audit (
            id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            secret_name  TEXT NOT NULL,
            requester    TEXT NOT NULL,
            context      TEXT NOT NULL,
            allowed      BOOLEAN NOT NULL,
            reason       TEXT NOT NULL DEFAULT '',
            resolved_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_cred_audit_name ON credentials_audit (secret_name)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_cred_audit_time ON credentials_audit (resolved_at DESC)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_cred_audit_time")
    op.execute("DROP INDEX IF EXISTS idx_cred_audit_name")
    op.execute("DROP TABLE IF EXISTS credentials_audit")
    op.execute("DROP INDEX IF EXISTS idx_credentials_name")
    op.execute("DROP TABLE IF EXISTS credentials")
