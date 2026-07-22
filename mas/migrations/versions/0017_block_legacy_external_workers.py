"""Block pre-governance external workers that cannot satisfy the contract.

Revision ID: 0017_block_legacy_external
Revises: 0016_worker_transition_history
Create Date: 2026-07-20

Existing local AIAT authority shells remain readable during the migration
window.  An external worker, however, must never remain active merely because
it predates the steward pipeline: it needs a pin, a certified adapter, and an
approved immutable skill bundle.  The new activation handler already enforces
that rule; this data migration closes the equivalent historical bypass.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0017_block_legacy_external"
down_revision = "0016_worker_transition_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE worker_registry
            SET
                status = 'INACTIVE',
                evaluation_status = 'blocked_legacy_wrapper',
                health_status = 'blocked',
                updated_at = NOW()
            WHERE status = 'ACTIVE'
              AND source_repo IS NOT NULL
              AND source_repo <> 'local'
              AND (
                    version_pin IS NULL
                 OR active_adapter_id IS NULL
                 OR active_skill_bundle_id IS NULL
              )
            """
        )
    )


def downgrade() -> None:
    # Do not silently reactivate a legacy external runtime: only the governed
    # activation endpoint may do that after certification and approval.
    pass
