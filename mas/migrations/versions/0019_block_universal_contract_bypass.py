"""Block registry rows that were active before governed version pointers.

Revision ID: 0019_block_universal_contract
Revises: 0018_ext_provenance_unique
Create Date: 2026-07-21

The original external-only migration left local/native and local-labelled
external wrappers ACTIVE even though they had no immutable WorkerShell,
certified Adapter, or approved Skill Bundle.  Those rows must stay readable
for migration, but they cannot be eligible for new universal Worker Runs.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0019_block_universal_contract"
down_revision = "0018_ext_provenance_unique"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE worker_registry
            SET
                status = 'INACTIVE',
                health_status = 'blocked',
                adapter_config = COALESCE(adapter_config, '{}'::jsonb) ||
                    jsonb_build_object(
                        'governance_required', true,
                        'governance_status', 'compatibility',
                        'activation_blockers', jsonb_build_array(
                            'missing immutable WorkerShell version',
                            'missing certified runtime Adapter',
                            'missing approved Skill Bundle and capability snapshot'
                        )
                    ),
                updated_at = NOW()
            WHERE status = 'ACTIVE'
              AND (
                    active_shell_version_id IS NULL
                 OR active_adapter_id IS NULL
                 OR active_skill_bundle_id IS NULL
              )
            """
        )
    )


def downgrade() -> None:
    # Do not silently reactivate rows.  A governed activation must validate
    # current certification, model policy, permissions, and readiness.
    pass
