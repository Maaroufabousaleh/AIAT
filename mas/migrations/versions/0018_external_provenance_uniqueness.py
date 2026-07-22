"""Make external provenance registration idempotent per worker.

Revision ID: 0018_ext_provenance_unique
Revises: 0017_block_legacy_external
"""

from __future__ import annotations

from alembic import op


revision = "0018_ext_provenance_unique"
down_revision = "0017_block_legacy_external"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Older development databases may contain duplicate rows from startup
    # re-registration. Keep the newest row for each worker before adding the
    # uniqueness guarantee used by the upsert path.
    op.execute(
        """
        DELETE FROM external_runtime_provenance old
        USING external_runtime_provenance newer
        WHERE old.worker_id = newer.worker_id
          AND (
                old.created_at < newer.created_at
             OR (old.created_at = newer.created_at AND old.id::text < newer.id::text)
          )
        """
    )
    op.create_unique_constraint(
        "uq_external_provenance_worker",
        "external_runtime_provenance",
        ["worker_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_external_provenance_worker",
        "external_runtime_provenance",
        type_="unique",
    )
