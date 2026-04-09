"""Add project_context_items table for file attachments

Revision ID: 0002_project_context
Revises: 0001_initial_schema
Create Date: 2026-04-07 00:00:00.000000

Tables
------
  1. project_context_items — file attachments and context materials for projects
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0002_project_context"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "project_context_items",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "project_id",
            sa.UUID(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "item_type",
            sa.Text(),
            nullable=False,
            comment="FILE | URL | TEXT | DOCUMENT",
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("mime_type", sa.Text()),
        sa.Column("size_bytes", sa.Integer()),
        sa.Column("blob_bucket", sa.Text()),
        sa.Column("blob_key", sa.Text()),
        sa.Column("blob_sha256", sa.Text()),
        sa.Column("url", sa.Text(), comment="For URL type items"),
        sa.Column("content_text", sa.Text(), comment="For TEXT type items"),
        sa.Column("metadata", sa.JSON(), comment="Additional metadata JSON"),
        sa.Column(
            "tags",
            sa.ARRAY(sa.Text()),
            comment="User-defined tags for organization",
        ),
        sa.Column(
            "created_by",
            sa.Text(),
            nullable=False,
            comment="agent_id or 'human'",
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("idx_context_project", "project_context_items", ["project_id"])
    op.create_index("idx_context_type", "project_context_items", ["item_type"])
    op.create_index("idx_context_tags", "project_context_items", ["tags"], postgresql_using="gin")


def downgrade() -> None:
    op.drop_table("project_context_items")
