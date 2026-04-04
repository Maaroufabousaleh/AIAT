"""SQLAlchemy Core table definitions — mirrors the Alembic migration 0001.

Why Core (not ORM)?
    PgBouncer in transaction-pooling mode forbids session-level state.
    SQLAlchemy Core + raw ``asyncpg`` is the lightest, safest layer.
    These table objects are used by :class:`AgentStorage` for composing queries
    and by Alembic ``env.py`` (via :pydata:`metadata`) for ``--autogenerate``.

Every table that stores agent-owned rows uses an ``agent_id`` or ``created_by``
column so that ``AgentStorage`` can filter automatically.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

metadata = sa.MetaData()

# ── 1. projects ───────────────────────────────────────────────────────────────
projects = sa.Table(
    "projects",
    metadata,
    sa.Column("id", sa.UUID(), primary_key=True),
    sa.Column("name", sa.Text(), nullable=False),
    sa.Column("description", sa.Text()),
    sa.Column("state", sa.Text(), nullable=False),
    sa.Column("failure_reason", sa.Text()),
    sa.Column("failed_from_state", sa.Text()),
    sa.Column("created_by", sa.Text(), nullable=False),
    sa.Column("human_requester", sa.Text()),
    sa.Column("config", JSONB()),
    sa.Column(
        "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
    sa.Column(
        "updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
)

# ── 2. project_state_history ──────────────────────────────────────────────────
project_state_history = sa.Table(
    "project_state_history",
    metadata,
    sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
    sa.Column(
        "project_id", sa.UUID(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    ),
    sa.Column("from_state", sa.Text()),
    sa.Column("to_state", sa.Text(), nullable=False),
    sa.Column("event", sa.Text(), nullable=False),
    sa.Column("triggered_by", sa.Text()),
    sa.Column("payload", JSONB()),
    sa.Column(
        "transitioned_at",
        sa.TIMESTAMP(timezone=True),
        server_default=sa.text("now()"),
        nullable=False,
    ),
)

# ── 3. documents ──────────────────────────────────────────────────────────────
documents = sa.Table(
    "documents",
    metadata,
    sa.Column("id", sa.UUID(), primary_key=True),
    sa.Column(
        "project_id", sa.UUID(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    ),
    sa.Column("doc_type", sa.Text(), nullable=False),
    sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    sa.Column("status", sa.Text(), nullable=False, server_default="DRAFT"),
    sa.Column("blob_bucket", sa.Text()),
    sa.Column("blob_key", sa.Text()),
    sa.Column("blob_sha256", sa.Text()),
    sa.Column("created_by", sa.Text(), nullable=False),
    sa.Column(
        "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
    sa.Column(
        "updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
)

# ── 4. review_sessions ───────────────────────────────────────────────────────
review_sessions = sa.Table(
    "review_sessions",
    metadata,
    sa.Column("id", sa.UUID(), primary_key=True),
    sa.Column(
        "project_id", sa.UUID(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    ),
    sa.Column("document_id", sa.UUID(), sa.ForeignKey("documents.id", ondelete="SET NULL")),
    sa.Column("session_type", sa.Text(), nullable=False),
    sa.Column("status", sa.Text(), nullable=False, server_default="IN_PROGRESS"),
    sa.Column("reviewer_ids", sa.ARRAY(sa.Text())),
    sa.Column("timeout_count", sa.Integer(), server_default="0", nullable=False),
    sa.Column("review_timeout_seconds", sa.Integer(), server_default="300", nullable=False),
    sa.Column(
        "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
    sa.Column("completed_at", sa.TIMESTAMP(timezone=True)),
)

# ── 5. review_comments ───────────────────────────────────────────────────────
review_comments = sa.Table(
    "review_comments",
    metadata,
    sa.Column("id", sa.UUID(), primary_key=True),
    sa.Column(
        "session_id",
        sa.UUID(),
        sa.ForeignKey("review_sessions.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("project_id", sa.UUID(), nullable=False),
    sa.Column("reviewer_id", sa.Text(), nullable=False),
    sa.Column("reviewer_role", sa.Text(), nullable=False),
    sa.Column("verdict", sa.Text(), nullable=False),
    sa.Column("veto", sa.Boolean(), server_default="false", nullable=False),
    sa.Column("severity", sa.Text()),
    sa.Column("comments", JSONB()),
    sa.Column(
        "submitted_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
)

# ── 6. approval_gates ────────────────────────────────────────────────────────
approval_gates = sa.Table(
    "approval_gates",
    metadata,
    sa.Column("id", sa.UUID(), primary_key=True),
    sa.Column(
        "project_id", sa.UUID(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    ),
    sa.Column("gate_type", sa.Text(), nullable=False),
    sa.Column("status", sa.Text(), nullable=False, server_default="PENDING"),
    sa.Column("decided_by", sa.Text()),
    sa.Column("justification", sa.Text()),
    sa.Column("human_input", JSONB()),
    sa.Column(
        "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
    sa.Column("decided_at", sa.TIMESTAMP(timezone=True)),
)

# ── 7. sprints ────────────────────────────────────────────────────────────────
sprints = sa.Table(
    "sprints",
    metadata,
    sa.Column("id", sa.UUID(), primary_key=True),
    sa.Column(
        "project_id", sa.UUID(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    ),
    sa.Column("sprint_number", sa.Integer(), nullable=False),
    sa.Column("milestone", sa.Text()),
    sa.Column("goal", sa.Text()),
    sa.Column("status", sa.Text(), nullable=False, server_default="PLANNED"),
    sa.Column("planned_story_points", sa.Integer()),
    sa.Column("completed_story_points", sa.Integer()),
    sa.Column("estimated_hours", sa.Numeric(10, 2)),
    sa.Column("actual_hours", sa.Numeric(10, 2)),
    sa.Column("start_date", sa.TIMESTAMP(timezone=True)),
    sa.Column("end_date", sa.TIMESTAMP(timezone=True)),
    sa.Column("infra_requested_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("infra_ready_at", sa.TIMESTAMP(timezone=True)),
    sa.Column(
        "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
)

# ── 8. issues ─────────────────────────────────────────────────────────────────
issues = sa.Table(
    "issues",
    metadata,
    sa.Column("id", sa.UUID(), primary_key=True),
    sa.Column(
        "project_id", sa.UUID(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    ),
    sa.Column("sprint_id", sa.UUID(), sa.ForeignKey("sprints.id", ondelete="SET NULL")),
    sa.Column("parent_issue_id", sa.UUID(), sa.ForeignKey("issues.id", ondelete="SET NULL")),
    sa.Column("title", sa.Text(), nullable=False),
    sa.Column("description", sa.Text()),
    sa.Column("issue_type", sa.Text(), nullable=False),
    sa.Column("status", sa.Text(), nullable=False, server_default="backlog"),
    sa.Column("priority", sa.Text(), nullable=False, server_default="medium"),
    sa.Column("assigned_team", sa.Text()),
    sa.Column("assigned_agent", sa.Text()),
    sa.Column("estimated_hours", sa.Numeric(10, 2)),
    sa.Column("actual_hours", sa.Numeric(10, 2)),
    sa.Column("story_points", sa.Integer()),
    sa.Column("dependencies", sa.ARRAY(sa.UUID())),
    sa.Column(
        "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
    sa.Column("completed_at", sa.TIMESTAMP(timezone=True)),
)

# ── 9. kpi_snapshots ─────────────────────────────────────────────────────────
kpi_snapshots = sa.Table(
    "kpi_snapshots",
    metadata,
    sa.Column("id", sa.UUID(), primary_key=True),
    sa.Column(
        "project_id", sa.UUID(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    ),
    sa.Column("sprint_id", sa.UUID(), sa.ForeignKey("sprints.id", ondelete="SET NULL")),
    sa.Column("scope", sa.Text(), nullable=False),
    sa.Column("estimation_accuracy", sa.Numeric(5, 4)),
    sa.Column("task_completion_rate", sa.Numeric(5, 4)),
    sa.Column("review_pass_rate", sa.Numeric(5, 4)),
    sa.Column("velocity", sa.Numeric(10, 2)),
    sa.Column("defect_rate", sa.Numeric(5, 4)),
    sa.Column("rework_rate", sa.Numeric(5, 4)),
    sa.Column("budget_adherence", sa.Numeric(5, 4)),
    sa.Column("resource_utilization", sa.Numeric(5, 4)),
    sa.Column("infra_lead_time_seconds", sa.Integer()),
    sa.Column("raw_data", JSONB()),
    sa.Column(
        "computed_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
)

# ── 10. agent_profiles ────────────────────────────────────────────────────────
agent_profiles = sa.Table(
    "agent_profiles",
    metadata,
    sa.Column("agent_id", sa.Text(), primary_key=True),
    sa.Column("team_id", sa.Text(), nullable=False),
    sa.Column("role", sa.Text(), nullable=False),
    sa.Column("correction_factor", sa.Numeric(5, 4), server_default="1.0", nullable=False),
    sa.Column("estimation_bias", sa.Numeric(5, 4), server_default="0.0", nullable=False),
    sa.Column("confidence", sa.Numeric(5, 4), server_default="0.5", nullable=False),
    sa.Column("total_tasks_completed", sa.Integer(), server_default="0", nullable=False),
    sa.Column("total_estimated_hours", sa.Numeric(12, 2), server_default="0"),
    sa.Column("total_actual_hours", sa.Numeric(12, 2), server_default="0"),
    sa.Column(
        "last_updated", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
)

# ── 11. dead_letters ──────────────────────────────────────────────────────────
dead_letters = sa.Table(
    "dead_letters",
    metadata,
    sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
    sa.Column("message_id", sa.Text(), nullable=False),
    sa.Column("recipient_team", sa.Text(), nullable=False),
    sa.Column("sender_id", sa.Text()),
    sa.Column("msg_type", sa.Text()),
    sa.Column("project_id", sa.UUID()),
    sa.Column("retry_count", sa.Integer(), nullable=False),
    sa.Column("failure_reason", sa.Text()),
    sa.Column("envelope_json", JSONB(), nullable=False),
    sa.Column(
        "dead_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
)

# ── 12. system_config ─────────────────────────────────────────────────────────
system_config = sa.Table(
    "system_config",
    metadata,
    sa.Column("key", sa.Text(), primary_key=True),
    sa.Column("value", sa.Text(), nullable=False),
    sa.Column("description", sa.Text()),
    sa.Column(
        "updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
)

# ── 13. agent_checkpoints ────────────────────────────────────────────────────
agent_checkpoints = sa.Table(
    "agent_checkpoints",
    metadata,
    sa.Column("id", sa.UUID(), primary_key=True),
    sa.Column("agent_id", sa.Text(), nullable=False),
    sa.Column("team_id", sa.Text(), nullable=False),
    sa.Column("project_id", sa.UUID()),
    sa.Column("task_message_id", sa.Text(), nullable=False),
    sa.Column("iteration", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("messages_json", JSONB(), nullable=False),
    sa.Column("tool_results_json", JSONB()),
    sa.Column("budget_state_json", JSONB()),
    sa.Column("task_envelope_json", JSONB(), nullable=False),
    sa.Column(
        "saved_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
    sa.UniqueConstraint("agent_id", "task_message_id", name="uq_checkpoint_agent_task"),
)

# ── 14. memory ────────────────────────────────────────────────────────────────
memory = sa.Table(
    "memory",
    metadata,
    sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
    sa.Column("agent_id", sa.Text(), nullable=False),
    sa.Column("key", sa.Text(), nullable=False),
    sa.Column("value", JSONB()),
    sa.Column(
        "updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
    sa.UniqueConstraint("agent_id", "key", name="uq_memory_agent_key"),
)

# ── 15. task_log ──────────────────────────────────────────────────────────────
task_log = sa.Table(
    "task_log",
    metadata,
    sa.Column("task_id", sa.UUID(), primary_key=True),
    sa.Column("agent_id", sa.Text(), nullable=False),
    sa.Column("parent_task_id", sa.UUID()),
    sa.Column("team_id", sa.Text(), nullable=False),
    sa.Column("status", sa.Text(), nullable=False),
    sa.Column("input", JSONB()),
    sa.Column("output", JSONB()),
    sa.Column("budget_snapshot", JSONB()),
    sa.Column("trace_id", sa.Text()),
    sa.Column(
        "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
    sa.Column(
        "updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
)

# ── 16. artifacts ─────────────────────────────────────────────────────────────
artifacts = sa.Table(
    "artifacts",
    metadata,
    sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
    sa.Column("agent_id", sa.Text(), nullable=False),
    sa.Column("path", sa.Text(), nullable=False),
    sa.Column("metadata", JSONB()),
    sa.Column("sha256", sa.Text()),
    sa.Column("size_bytes", sa.BigInteger()),
    sa.Column(
        "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
)

# ── 17. infra_events ──────────────────────────────────────────────────────────
infra_events = sa.Table(
    "infra_events",
    metadata,
    sa.Column("id", sa.UUID(), primary_key=True),
    sa.Column(
        "project_id", sa.UUID(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    ),
    sa.Column("sprint_id", sa.UUID(), sa.ForeignKey("sprints.id", ondelete="SET NULL")),
    sa.Column("event_type", sa.Text(), nullable=False),
    sa.Column("details", JSONB()),
    sa.Column(
        "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
)

# ── 18. capabilities ─────────────────────────────────────────────────────────
capabilities = sa.Table(
    "capabilities",
    metadata,
    sa.Column("id", sa.UUID(), primary_key=True),
    sa.Column("name", sa.Text(), nullable=False),
    sa.Column("version", sa.Text(), nullable=False, server_default="'1.0'"),
    sa.Column("description", sa.Text()),
    sa.Column("input_schema", JSONB()),
    sa.Column("output_schema", JSONB()),
    sa.Column("risk_level", sa.Text(), nullable=False, server_default="'low'"),
    sa.Column("cost_model", JSONB()),
    sa.Column("required_tools", sa.ARRAY(sa.Text()), server_default="{}"),
    sa.Column("required_role", sa.Text()),
    sa.Column(
        "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
    sa.UniqueConstraint("name", name="uq_capabilities_name"),
)

# ── 19. worker_registry ───────────────────────────────────────────────────────
worker_registry = sa.Table(
    "worker_registry",
    metadata,
    sa.Column("id", sa.UUID(), primary_key=True),
    sa.Column("name", sa.Text(), nullable=False),
    sa.Column("adapter_type", sa.Text(), nullable=False),
    sa.Column("adapter_config", JSONB(), nullable=False, server_default="{}"),
    sa.Column("sandbox_profile", sa.Text(), nullable=False, server_default="'standard'"),
    sa.Column("capability_ids", sa.ARRAY(sa.UUID()), nullable=False, server_default="{}"),
    sa.Column("team_id", sa.Text()),
    sa.Column("status", sa.Text(), nullable=False, server_default="'ACTIVE'"),
    sa.Column(
        "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
    sa.Column(
        "updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
    sa.UniqueConstraint("name", name="uq_worker_registry_name"),
)

# ── 20. role_capability_map ───────────────────────────────────────────────────
role_capability_map = sa.Table(
    "role_capability_map",
    metadata,
    sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
    sa.Column("role", sa.Text(), nullable=False),
    sa.Column(
        "capability_id",
        sa.UUID(),
        sa.ForeignKey("capabilities.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("constraints", JSONB()),
    sa.UniqueConstraint("role", "capability_id", name="uq_role_capability"),
)

# ── 21. flows ─────────────────────────────────────────────────────────────────
flows = sa.Table(
    "flows",
    metadata,
    sa.Column("id", sa.UUID(), primary_key=True),
    sa.Column("name", sa.Text(), nullable=False),
    sa.Column("description", sa.Text()),
    sa.Column("definition_json", JSONB(), nullable=False, server_default="{}"),
    sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    sa.Column("created_by", sa.Text(), nullable=False, server_default="system"),
    sa.Column("is_active", sa.Boolean(), nullable=False, server_default="false"),
    sa.Column(
        "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
    sa.Column(
        "updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
)

# ── 22. flow_instances ────────────────────────────────────────────────────────
flow_instances = sa.Table(
    "flow_instances",
    metadata,
    sa.Column("id", sa.UUID(), primary_key=True),
    sa.Column("flow_id", sa.UUID(), sa.ForeignKey("flows.id", ondelete="RESTRICT"), nullable=False),
    sa.Column("flow_version", sa.Integer(), nullable=False),
    sa.Column(
        "project_id", sa.UUID(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    ),
    sa.Column("active_node_ids", sa.ARRAY(sa.Text()), nullable=False, server_default="{}"),
    sa.Column("status", sa.Text(), nullable=False, server_default="NOT_STARTED"),
    sa.Column("context_json", JSONB(), nullable=False, server_default="{}"),
    sa.Column("started_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("completed_at", sa.TIMESTAMP(timezone=True)),
    sa.Column(
        "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
    sa.Column(
        "updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
)

# ── 23. flow_node_executions ─────────────────────────────────────────────────
flow_node_executions = sa.Table(
    "flow_node_executions",
    metadata,
    sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
    sa.Column(
        "instance_id",
        sa.UUID(),
        sa.ForeignKey("flow_instances.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("node_id", sa.Text(), nullable=False),
    sa.Column("node_type", sa.Text(), nullable=False),
    sa.Column("node_label", sa.Text()),
    sa.Column("status", sa.Text(), nullable=False, server_default="RUNNING"),
    sa.Column("input_json", JSONB()),
    sa.Column("output_json", JSONB()),
    sa.Column("error", sa.Text()),
    sa.Column(
        "started_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
    sa.Column("completed_at", sa.TIMESTAMP(timezone=True)),
)
