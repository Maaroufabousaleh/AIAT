"""add missing tables (memory, task_log, artifacts, infra_events) and projects columns

Revision ID: 0002_missing_tables
Revises: 0001_initial_schema
Create Date: 2026-02-27 00:00:00.000000

Tables added
------------
14. memory          — per-agent key/value store (JSONB values)
15. task_log        — task execution audit trail
16. artifacts       — blob metadata for agent-produced artifacts
17. infra_events    — DevOps INFRA_READY / INFRA_FAILED tracking

Columns added
-------------
- projects.human_requester TEXT        — human identifier
- projects.config          JSONB       — project-level settings
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0002_missing_tables"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── projects: add missing columns ─────────────────────────────────────────
    op.add_column(
        "projects",
        sa.Column("human_requester", sa.Text(), comment="Human identifier"),
    )
    op.add_column(
        "projects",
        sa.Column("config", JSONB(), comment="Project-level settings"),
    )

    # ── 14. memory ────────────────────────────────────────────────────────────
    op.create_table(
        "memory",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("agent_id", sa.Text(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("value", JSONB()),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("agent_id", "key", name="uq_memory_agent_key"),
    )
    op.create_index("idx_memory_agent", "memory", ["agent_id"])

    # ── 15. task_log ──────────────────────────────────────────────────────────
    op.create_table(
        "task_log",
        sa.Column("task_id", sa.UUID(), primary_key=True),
        sa.Column("agent_id", sa.Text(), nullable=False),
        sa.Column("parent_task_id", sa.UUID()),
        sa.Column("team_id", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            comment="PENDING | RUNNING | COMPLETED | FAILED | CANCELLED",
        ),
        sa.Column("input", JSONB(), comment="Task input payload"),
        sa.Column("output", JSONB(), comment="Task output / result"),
        sa.Column("budget_snapshot", JSONB(), comment="Budget state at completion"),
        sa.Column("trace_id", sa.Text(), comment="Distributed tracing correlation"),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("idx_task_log_agent", "task_log", ["agent_id"])
    op.create_index("idx_task_log_team", "task_log", ["team_id"])
    op.create_index("idx_task_log_trace", "task_log", ["trace_id"])

    # ── 16. artifacts ─────────────────────────────────────────────────────────
    op.create_table(
        "artifacts",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("agent_id", sa.Text(), nullable=False),
        sa.Column("path", sa.Text(), nullable=False, comment="Blob key / virtual path"),
        sa.Column("metadata", JSONB(), comment="Arbitrary metadata"),
        sa.Column("sha256", sa.Text(), comment="Content hash"),
        sa.Column("size_bytes", sa.BigInteger(), comment="Content size in bytes"),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("idx_artifacts_agent", "artifacts", ["agent_id"])

    # ── 17. infra_events ──────────────────────────────────────────────────────
    op.create_table(
        "infra_events",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "project_id",
            sa.UUID(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sprint_id", sa.UUID(), sa.ForeignKey("sprints.id", ondelete="SET NULL")),
        sa.Column(
            "event_type",
            sa.Text(),
            nullable=False,
            comment="INFRA_REQUESTED | INFRA_READY | INFRA_FAILED",
        ),
        sa.Column("details", JSONB()),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("idx_infra_events_project", "infra_events", ["project_id"])


def downgrade() -> None:
    op.drop_table("infra_events")
    op.drop_table("artifacts")
    op.drop_table("task_log")
    op.drop_table("memory")
    op.drop_column("projects", "config")
    op.drop_column("projects", "human_requester")
