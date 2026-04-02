"""initial schema — all 13 MAS tables

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-02-26 00:00:00.000000

Tables
------
 1. projects                — workflow state machine records
 2. project_state_history   — immutable audit trail of state transitions
 3. documents               — document metadata (content body in MinIO)
 4. review_sessions         — parallel review fan-out tracking
 5. review_comments         — per-reviewer response within a session
 6. approval_gates          — human decision gates + CSO override audit
 7. sprints                 — CTO-managed sprint records
 8. issues                  — task/feature/QA/infra issue tracking
 9. kpi_snapshots           — per-sprint/per-project KPI metrics
10. agent_profiles          — per-agent estimation correction factors
11. dead_letters            — DLQ: messages that exhausted delivery retries
12. system_config           — system lifecycle state + working-hours schedule
13. agent_checkpoints       — mid-task LLM conversation checkpoints (resumable)
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. projects ───────────────────────────────────────────────────────────
    op.create_table(
        "projects",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column(
            "state",
            sa.Text(),
            nullable=False,
            comment=(
                "ProjectState enum: INIT | FEASIBILITY_CHECK | FEASIBILITY_REPORT | "
                "PDR_CREATION | PDR_REVIEW | SECURITY_BLOCKED | CDR_CREATION | "
                "CDR_REVIEW | HUMAN_APPROVAL | RR_CREATION | SPRINT_PLANNING | "
                "INFRA_PROVISIONING | IN_PROGRESS | RETROSPECTIVE | "
                "KPI_PERSISTENCE | COMPLETED | ARCHIVED | FAILED"
            ),
        ),
        sa.Column("failure_reason", sa.Text(), comment="Set when state=FAILED"),
        sa.Column("failed_from_state", sa.Text(), comment="State active at failure time"),
        sa.Column("created_by", sa.Text(), nullable=False, comment="agent_id of CEO"),
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
            comment="Updated by controller on every state transition",
        ),
    )
    op.create_index("idx_projects_state", "projects", ["state"])

    # ── 2. project_state_history ──────────────────────────────────────────────
    op.create_table(
        "project_state_history",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "project_id",
            sa.UUID(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("from_state", sa.Text()),
        sa.Column("to_state", sa.Text(), nullable=False),
        sa.Column(
            "event", sa.Text(), nullable=False, comment="EventType that triggered transition"
        ),
        sa.Column("triggered_by", sa.Text(), comment="agent_id that emitted the event"),
        sa.Column("payload", JSONB(), comment="Event payload snapshot"),
        sa.Column(
            "transitioned_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("idx_state_history_project", "project_state_history", ["project_id"])

    # ── 3. documents ──────────────────────────────────────────────────────────
    op.create_table(
        "documents",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "project_id",
            sa.UUID(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "doc_type",
            sa.Text(),
            nullable=False,
            comment="PDR | CDR | RR | TEST_PLAN | SPRINT_REPORT | RETROSPECTIVE",
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default="DRAFT",
            comment="DRAFT | IN_REVIEW | APPROVED | NEEDS_REVISION | SUPERSEDED",
        ),
        sa.Column("blob_bucket", sa.Text(), comment="MinIO bucket"),
        sa.Column("blob_key", sa.Text(), comment="MinIO object key"),
        sa.Column("blob_sha256", sa.Text(), comment="Content hash for integrity checks"),
        sa.Column("created_by", sa.Text(), nullable=False, comment="agent_id"),
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
    op.create_index("idx_documents_project_type", "documents", ["project_id", "doc_type"])

    # ── 4. review_sessions ────────────────────────────────────────────────────
    op.create_table(
        "review_sessions",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "project_id",
            sa.UUID(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("document_id", sa.UUID(), sa.ForeignKey("documents.id", ondelete="SET NULL")),
        sa.Column(
            "session_type",
            sa.Text(),
            nullable=False,
            comment="FEASIBILITY | PDR | CDR",
        ),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default="IN_PROGRESS",
            comment="IN_PROGRESS | COMPLETED | TIMED_OUT | CIRCUIT_OPEN",
        ),
        sa.Column(
            "reviewer_ids", sa.ARRAY(sa.Text()), comment="List of agent_ids invited to review"
        ),
        sa.Column("timeout_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("review_timeout_seconds", sa.Integer(), server_default="300", nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True)),
    )
    op.create_index("idx_review_sessions_project", "review_sessions", ["project_id"])

    # ── 5. review_comments ────────────────────────────────────────────────────
    op.create_table(
        "review_comments",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "session_id",
            sa.UUID(),
            sa.ForeignKey("review_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("reviewer_id", sa.Text(), nullable=False, comment="agent_id"),
        sa.Column("reviewer_role", sa.Text(), nullable=False, comment="AgentRole"),
        sa.Column(
            "verdict",
            sa.Text(),
            nullable=False,
            comment="APPROVED | NEEDS_REVISION | BLOCKER",
        ),
        sa.Column(
            "veto", sa.Boolean(), server_default="false", nullable=False, comment="CSO veto flag"
        ),
        sa.Column(
            "severity",
            sa.Text(),
            comment="INFO | WARNING | BLOCKER — per reviewer",
        ),
        sa.Column("comments", JSONB(), comment="List of {section, body, suggested_change}"),
        sa.Column(
            "submitted_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("idx_review_comments_session", "review_comments", ["session_id"])

    # ── 6. approval_gates ─────────────────────────────────────────────────────
    op.create_table(
        "approval_gates",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "project_id",
            sa.UUID(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "gate_type",
            sa.Text(),
            nullable=False,
            comment="FEASIBILITY | CDR_APPROVAL | CEO_OVERRIDE_CSO",
        ),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default="PENDING",
            comment="PENDING | APPROVED | REJECTED | OVERRIDDEN",
        ),
        sa.Column("decided_by", sa.Text(), comment="agent_id or 'human'"),
        sa.Column("justification", sa.Text(), comment="Mandatory for CEO_OVERRIDE_CSO"),
        sa.Column("human_input", JSONB(), comment="Raw human decision payload"),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("decided_at", sa.TIMESTAMP(timezone=True)),
    )
    op.create_index("idx_approval_gates_project", "approval_gates", ["project_id"])

    # ── 7. sprints ────────────────────────────────────────────────────────────
    op.create_table(
        "sprints",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "project_id",
            sa.UUID(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sprint_number", sa.Integer(), nullable=False),
        sa.Column("milestone", sa.Text()),
        sa.Column("goal", sa.Text()),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default="PLANNED",
            comment="PLANNED | BLOCKED | ACTIVE | COMPLETED | CANCELLED",
        ),
        sa.Column("planned_story_points", sa.Integer()),
        sa.Column("completed_story_points", sa.Integer()),
        sa.Column("estimated_hours", sa.Numeric(10, 2)),
        sa.Column("actual_hours", sa.Numeric(10, 2)),
        sa.Column("start_date", sa.TIMESTAMP(timezone=True)),
        sa.Column("end_date", sa.TIMESTAMP(timezone=True)),
        sa.Column("infra_requested_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("infra_ready_at", sa.TIMESTAMP(timezone=True)),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("idx_sprints_project", "sprints", ["project_id"])

    # ── 8. issues ─────────────────────────────────────────────────────────────
    op.create_table(
        "issues",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "project_id",
            sa.UUID(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sprint_id", sa.UUID(), sa.ForeignKey("sprints.id", ondelete="SET NULL")),
        sa.Column("parent_issue_id", sa.UUID(), sa.ForeignKey("issues.id", ondelete="SET NULL")),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column(
            "issue_type",
            sa.Text(),
            nullable=False,
            comment="FEATURE | TEST | QA | DOCS | INFRA",
        ),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default="OPEN",
            comment="OPEN | IN_PROGRESS | DONE | CANCELLED",
        ),
        sa.Column(
            "priority",
            sa.Text(),
            nullable=False,
            server_default="MEDIUM",
            comment="CRITICAL | HIGH | MEDIUM | LOW",
        ),
        sa.Column("assigned_team", sa.Text(), comment="team_id — which team handles this issue"),
        sa.Column("assigned_agent", sa.Text(), comment="agent_id if directly assigned"),
        sa.Column("estimated_hours", sa.Numeric(10, 2)),
        sa.Column("actual_hours", sa.Numeric(10, 2)),
        sa.Column("story_points", sa.Integer()),
        sa.Column("dependencies", sa.ARRAY(sa.UUID()), comment="issue ids this blocks on"),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True)),
    )
    op.create_index("idx_issues_project", "issues", ["project_id"])
    op.create_index("idx_issues_sprint", "issues", ["sprint_id"])
    op.create_index("idx_issues_type", "issues", ["issue_type"])

    # ── 9. kpi_snapshots ──────────────────────────────────────────────────────
    op.create_table(
        "kpi_snapshots",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "project_id",
            sa.UUID(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sprint_id", sa.UUID(), sa.ForeignKey("sprints.id", ondelete="SET NULL")),
        sa.Column(
            "scope",
            sa.Text(),
            nullable=False,
            comment="SPRINT | PROJECT",
        ),
        # Core KPI fields (null = not yet computed for this scope)
        sa.Column("estimation_accuracy", sa.Numeric(5, 4), comment="0–1; 1=perfect"),
        sa.Column("task_completion_rate", sa.Numeric(5, 4)),
        sa.Column("review_pass_rate", sa.Numeric(5, 4)),
        sa.Column("velocity", sa.Numeric(10, 2), comment="story_points / sprint_duration_days"),
        sa.Column("defect_rate", sa.Numeric(5, 4)),
        sa.Column("rework_rate", sa.Numeric(5, 4)),
        sa.Column("budget_adherence", sa.Numeric(5, 4)),
        sa.Column("resource_utilization", sa.Numeric(5, 4)),
        sa.Column(
            "infra_lead_time_seconds", sa.Integer(), comment="infra_ready_at - infra_requested_at"
        ),
        sa.Column("raw_data", JSONB(), comment="Full raw numbers for future recomputation"),
        sa.Column(
            "computed_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("idx_kpi_project", "kpi_snapshots", ["project_id"])

    # ── 10. agent_profiles ────────────────────────────────────────────────────
    op.create_table(
        "agent_profiles",
        sa.Column("agent_id", sa.Text(), primary_key=True),
        sa.Column("team_id", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False, comment="AgentRole"),
        sa.Column(
            "correction_factor",
            sa.Numeric(5, 4),
            server_default="1.0",
            nullable=False,
            comment="Multiply estimated hours by this to get adjusted estimate",
        ),
        sa.Column("estimation_bias", sa.Numeric(5, 4), server_default="0.0", nullable=False),
        sa.Column(
            "confidence",
            sa.Numeric(5, 4),
            server_default="0.5",
            nullable=False,
            comment="0–1; increases as more data is collected",
        ),
        sa.Column("total_tasks_completed", sa.Integer(), server_default="0", nullable=False),
        sa.Column("total_estimated_hours", sa.Numeric(12, 2), server_default="0"),
        sa.Column("total_actual_hours", sa.Numeric(12, 2), server_default="0"),
        sa.Column(
            "last_updated",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    # ── 11. dead_letters ──────────────────────────────────────────────────────
    # Messages that exhausted delivery retries (retry_count >= max_attempts
    # OR TTL expired before any delivery).
    op.create_table(
        "dead_letters",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("message_id", sa.Text(), nullable=False, comment="envelope.message_id (ULID)"),
        sa.Column("recipient_team", sa.Text(), nullable=False),
        sa.Column("sender_id", sa.Text()),
        sa.Column("msg_type", sa.Text()),
        sa.Column("project_id", sa.UUID()),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("failure_reason", sa.Text(), comment="RETRY_EXHAUSTED | TTL_EXPIRED"),
        sa.Column(
            "envelope_json",
            JSONB(),
            nullable=False,
            comment="Full serialised envelope for forensics",
        ),
        sa.Column(
            "dead_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("idx_dead_letters_project", "dead_letters", ["project_id"])
    op.create_index("idx_dead_letters_team", "dead_letters", ["recipient_team"])

    # ── 12. system_config ─────────────────────────────────────────────────────
    op.create_table(
        "system_config",
        sa.Column("key", sa.Text(), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    # Bootstrap rows
    op.execute("""
        INSERT INTO system_config (key, value, description) VALUES
            ('system_state',    'STOPPED',  'RUNNING | SHUTTING_DOWN | STARTING | STOPPED'),
            ('boot_at',          '',         'Timestamp of last successful startup (set by orchestrator-api)'),
            ('shutdown_at',      '',         'Timestamp of last clean shutdown'),
            ('watchdog_timeout', '3600',     'Seconds of inactivity before project is marked FAILED'),
            ('watchdog_grace',   '300',      'Seconds to ignore watchdog after system boot'),
            ('schedule_enabled', 'false',    'Enable automatic shutdown / resume on a daily schedule'),
            ('schedule_tz',      'UTC',      'IANA timezone for schedule_active_start / schedule_active_end'),
            ('schedule_active_start', '08:00', 'HH:MM — start of active window'),
            ('schedule_active_end',   '20:00', 'HH:MM — end of active window (auto-shutdown)'),
            ('infra_provisioning_sla', '1800', 'Seconds before watchdog escalates INFRA_PROVISIONING delay'),
            ('review_timeout',   '300',      'Seconds before a reviewer is considered timed out'),
            ('max_review_timeouts', '2',     'Circuit breaker: how many timeouts before FAILED(REVIEW_CIRCUIT_OPEN)')
        ON CONFLICT (key) DO NOTHING;
    """)

    # ── 13. agent_checkpoints ─────────────────────────────────────────────────
    op.create_table(
        "agent_checkpoints",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("agent_id", sa.Text(), nullable=False),
        sa.Column("team_id", sa.Text(), nullable=False),
        sa.Column("project_id", sa.UUID()),
        sa.Column(
            "task_message_id", sa.Text(), nullable=False, comment="message_id of the TASK envelope"
        ),
        sa.Column("iteration", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "messages_json",
            JSONB(),
            nullable=False,
            comment="Full LLM chat history up to this checkpoint",
        ),
        sa.Column("tool_results_json", JSONB(), comment="Accumulated tool results"),
        sa.Column("budget_state_json", JSONB(), comment="Remaining budget counters"),
        sa.Column(
            "task_envelope_json", JSONB(), nullable=False, comment="Original TASK envelope"
        ),
        sa.Column(
            "saved_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("idx_checkpoints_agent", "agent_checkpoints", ["agent_id"])
    op.create_index("idx_checkpoints_project", "agent_checkpoints", ["project_id"])
    # Unique: each (agent, task) pair has at most one checkpoint row.
    op.create_unique_constraint(
        "uq_checkpoint_agent_task", "agent_checkpoints", ["agent_id", "task_message_id"]
    )


def downgrade() -> None:
    op.drop_table("agent_checkpoints")
    op.drop_table("system_config")
    op.drop_table("dead_letters")
    op.drop_table("agent_profiles")
    op.drop_table("kpi_snapshots")
    op.drop_table("issues")
    op.drop_table("sprints")
    op.drop_table("approval_gates")
    op.drop_table("review_comments")
    op.drop_table("review_sessions")
    op.drop_table("documents")
    op.drop_table("project_state_history")
    op.drop_table("projects")
