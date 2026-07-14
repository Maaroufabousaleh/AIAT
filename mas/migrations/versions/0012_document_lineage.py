"""Add stable lineage identifiers for immutable document revisions.

Revision ID: 0012_document_lineage
Revises: 0011_guarded_worker_evaluation
Create Date: 2026-07-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012_document_lineage"
down_revision = "0011_guarded_worker_evaluation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("lineage_id", sa.UUID(), nullable=True))
    # Existing version-one rows are their own lineage roots.  New revisions
    # inherit this value instead of searching by project and document type.
    op.execute("UPDATE documents SET lineage_id = id WHERE lineage_id IS NULL")
    op.alter_column("documents", "lineage_id", nullable=False)
    op.create_index("idx_documents_lineage_version", "documents", ["lineage_id", "version"])


def downgrade() -> None:
    op.drop_index("idx_documents_lineage_version", table_name="documents")
    op.drop_column("documents", "lineage_id")
