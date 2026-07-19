"""Add durable project-scoped LLM and tool usage events.

Revision ID: 0014_project_usage_events
Revises: 0013_terminal_gate_cleanup
Create Date: 2026-07-18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0014_project_usage_events"
down_revision = "0013_terminal_gate_cleanup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "project_usage_events",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "project_id",
            sa.UUID(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("agent_id", sa.Text()),
        sa.Column("team_id", sa.Text()),
        sa.Column("model", sa.Text()),
        sa.Column("tool_name", sa.Text()),
        sa.Column("status", sa.Text(), nullable=False, server_default="success"),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Numeric(14, 8), nullable=False, server_default="0"),
        sa.Column("duration_ms", sa.Numeric(14, 3)),
        sa.Column("trace_id", sa.Text()),
        sa.Column("span_id", sa.Text()),
        sa.Column("details", JSONB()),
        sa.Column(
            "occurred_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "event_type IN ('llm', 'tool')",
            name="ck_project_usage_events_event_type",
        ),
    )
    op.create_index(
        "idx_project_usage_events_project_time",
        "project_usage_events",
        ["project_id", "occurred_at"],
    )
    op.create_index(
        "idx_project_usage_events_project_type",
        "project_usage_events",
        ["project_id", "event_type"],
    )


def downgrade() -> None:
    op.drop_index("idx_project_usage_events_project_type", table_name="project_usage_events")
    op.drop_index("idx_project_usage_events_project_time", table_name="project_usage_events")
    op.drop_table("project_usage_events")
