"""Add pgvector extension and update content_vector to vector type

Revision ID: 0008_pgvector_support
Revises: 0007_worker_integration
Create Date: 2026-04-08 00:00:00.000000

This migration adds pgvector support for semantic search over project context chunks.
- Enables pgvector extension
- Converts content_vector from JSONB to proper vector(n) type
- Creates HNSW index for cosine similarity search

Note: This migration requires pgvector to be installed in the PostgreSQL instance.
"""

from __future__ import annotations

from alembic import op

revision: str = "0008_pgvector_support"
down_revision = ("0007_worker_integration", "0003_project_context_knowledge")
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.execute("""
        ALTER TABLE project_context_chunks 
        ALTER COLUMN content_vector TYPE text
    """)

    op.execute("""
        ALTER TABLE project_context_chunks 
        ALTER COLUMN content_vector TYPE vector(1536)
        USING (content_vector::vector)
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_chunks_content_vector_hnsw 
        ON project_context_chunks 
        USING hnsw (content_vector vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_chunks_project_id
        ON project_context_chunks (project_id)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_chunks_project_id")
    op.execute("DROP INDEX IF EXISTS idx_chunks_content_vector_hnsw")

    op.execute("""
        ALTER TABLE project_context_chunks 
        ALTER COLUMN content_vector TYPE jsonb
        USING (content_vector::text::jsonb)
    """)
