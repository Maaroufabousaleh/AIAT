"""Add project context knowledge layer tables

Revision ID: 0003_project_context_knowledge
Revises: 0002_project_context
Create Date: 2026-04-07 00:00:00.000000

Tables
------
  1. project_context_chunks   — chunked text for RAG with optional embeddings
  2. project_context_tags     — centralized tag management
  3. project_context_relations — links between context items
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0003_project_context_knowledge"
down_revision = "0002_project_context"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "project_context_chunks",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "context_item_id",
            sa.UUID(),
            sa.ForeignKey("project_context_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content_text", sa.Text(), nullable=False),
        sa.Column(
            "content_vector", sa.JSON(), comment="Embedding vector array for semantic search"
        ),
        sa.Column(
            "source_location", sa.Text(), comment="Where in the source file this chunk came from"
        ),
        sa.Column("metadata", sa.JSON(), comment="Chunk-level metadata"),
        sa.Column("token_count", sa.Integer()),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("idx_chunks_item", "project_context_chunks", ["context_item_id"])
    op.create_index("idx_chunks_project", "project_context_chunks", ["project_id"])

    op.create_table(
        "project_context_tags",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("color", sa.Text()),
        sa.Column("description", sa.Text()),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("idx_tags_project", "project_context_tags", ["project_id"])
    op.create_unique_constraint(
        "uq_tags_project_name", "project_context_tags", ["project_id", "name"]
    )

    op.create_table(
        "project_context_relations",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column(
            "source_item_id",
            sa.UUID(),
            sa.ForeignKey("project_context_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "target_item_id",
            sa.UUID(),
            sa.ForeignKey("project_context_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("relation_type", sa.Text(), nullable=False),
        sa.Column("metadata", sa.JSON()),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("idx_relations_project", "project_context_relations", ["project_id"])
    op.create_index("idx_relations_source", "project_context_relations", ["source_item_id"])
    op.create_index("idx_relations_target", "project_context_relations", ["target_item_id"])


def downgrade() -> None:
    op.drop_table("project_context_relations")
    op.drop_table("project_context_tags")
    op.drop_table("project_context_chunks")
