"""Add bounded native trace spans for AIAT service boundaries."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0036_native_trace_spans"
down_revision = "0035_trace_correlation_evidence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "native_trace_spans",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("trace_id", sa.Text(), nullable=False),
        sa.Column("span_id", sa.Text(), nullable=False),
        sa.Column("parent_span_id", sa.Text()),
        sa.Column("source_kind", sa.Text(), nullable=False),
        sa.Column("operation", sa.Text(), nullable=False),
        sa.Column("service", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="unknown"),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("ended_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("duration_ms", sa.Numeric(14, 3), nullable=False, server_default="0"),
        sa.Column("sampled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("retention_until", sa.TIMESTAMP(timezone=True)),
        sa.Column("attributes_json", JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("trace_id", "span_id", name="uq_native_trace_span"),
        sa.CheckConstraint(
            "source_kind IN ('transport', 'model', 'tool', 'mail', 'audit', 'worker', 'integration')",
            name="ck_native_trace_span_source_kind",
        ),
        sa.CheckConstraint(
            "status IN ('success', 'failure', 'unknown')",
            name="ck_native_trace_span_status",
        ),
        sa.CheckConstraint("duration_ms >= 0", name="ck_native_trace_span_duration"),
    )
    op.create_index("ix_native_trace_spans_trace_id", "native_trace_spans", ["trace_id"])
    op.create_index(
        "ix_native_trace_spans_started_at",
        "native_trace_spans",
        ["started_at"],
    )
    op.create_index(
        "ix_native_trace_spans_kind_time",
        "native_trace_spans",
        ["source_kind", "started_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_native_trace_spans_kind_time", table_name="native_trace_spans")
    op.drop_index("ix_native_trace_spans_started_at", table_name="native_trace_spans")
    op.drop_index("ix_native_trace_spans_trace_id", table_name="native_trace_spans")
    op.drop_table("native_trace_spans")
