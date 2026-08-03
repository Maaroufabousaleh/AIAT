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
    sa.Column("revision", sa.BigInteger(), nullable=False, server_default="1"),
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
    sa.Column(
        "lineage_id",
        sa.UUID(),
        nullable=False,
        comment="Stable root identifier shared by all immutable document revisions",
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
    sa.Column("revision", sa.BigInteger(), nullable=False, server_default="1"),
    sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
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
    sa.Column("revision", sa.BigInteger(), nullable=False, server_default="1"),
    sa.Column(
        "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
    sa.Column(
        "updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
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

# ── 14. project_context_items ─────────────────────────────────────────────────
project_context_items = sa.Table(
    "project_context_items",
    metadata,
    sa.Column("id", sa.UUID(), primary_key=True),
    sa.Column(
        "project_id", sa.UUID(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    ),
    sa.Column("item_type", sa.Text(), nullable=False),
    sa.Column("name", sa.Text(), nullable=False),
    sa.Column("description", sa.Text()),
    sa.Column("mime_type", sa.Text()),
    sa.Column("size_bytes", sa.Integer()),
    sa.Column("blob_bucket", sa.Text()),
    sa.Column("blob_key", sa.Text()),
    sa.Column("blob_sha256", sa.Text()),
    sa.Column("url", sa.Text()),
    sa.Column("content_text", sa.Text()),
    sa.Column("metadata", JSONB()),
    sa.Column("tags", sa.ARRAY(sa.Text())),
    sa.Column("created_by", sa.Text(), nullable=False),
    sa.Column(
        "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
)

# ── 14a. project_context_chunks ──────────────────────────────────────────────
project_context_chunks = sa.Table(
    "project_context_chunks",
    metadata,
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
    sa.Column("content_vector", JSONB()),
    sa.Column("source_location", sa.Text()),
    sa.Column("metadata", JSONB()),
    sa.Column("token_count", sa.Integer()),
    sa.Column(
        "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
)

# ── 14b. project_context_tags ────────────────────────────────────────────────
project_context_tags = sa.Table(
    "project_context_tags",
    metadata,
    sa.Column("id", sa.UUID(), primary_key=True),
    sa.Column("project_id", sa.UUID(), nullable=False),
    sa.Column("name", sa.Text(), nullable=False),
    sa.Column("color", sa.Text()),
    sa.Column("description", sa.Text()),
    sa.Column(
        "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
    sa.UniqueConstraint("project_id", "name", name="uq_tags_project_name"),
)

# ── 14c. project_context_relations ──────────────────────────────────────────
project_context_relations = sa.Table(
    "project_context_relations",
    metadata,
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
    sa.Column("metadata", JSONB()),
    sa.Column(
        "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
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
    sa.Column("version", sa.Text(), nullable=True),
    sa.Column("source_repo", sa.Text(), nullable=True),
    sa.Column("source_revision", sa.Text(), nullable=True),
    sa.Column("version_pin", sa.Text(), nullable=True),
    sa.Column("update_policy", sa.Text(), nullable=False, server_default="'manual'"),
    sa.Column("evaluation_status", sa.Text(), nullable=True),
    sa.Column("adapter_entrypoint", sa.Text(), nullable=False, server_default="'WorkerAgent'"),
    sa.Column("adapter_module", sa.Text(), nullable=True),
    sa.Column("wrapper_config", JSONB(), nullable=False, server_default="{}"),
    sa.Column("isolation_mode", sa.Text(), nullable=False, server_default="'native'"),
    # Mutable pointers to immutable governed versions.  Worker runs copy the
    # selected IDs at dispatch time, so changing these pointers never rewrites
    # historical execution state.
    sa.Column("active_shell_version_id", sa.UUID(), nullable=True),
    sa.Column("active_adapter_id", sa.UUID(), nullable=True),
    sa.Column("active_skill_bundle_id", sa.UUID(), nullable=True),
    sa.Column("model_profile_id", sa.Text(), nullable=True),
    sa.Column("model_mode", sa.Text(), nullable=False, server_default="'none'"),
    sa.Column("last_upstream_sync", sa.TIMESTAMP(timezone=True), nullable=True),
    sa.Column("upstream_commit_sha", sa.Text(), nullable=True),
    sa.Column("health_status", sa.Text(), nullable=False, server_default="'unknown'"),
    sa.Column("last_seen_at", sa.TIMESTAMP(timezone=True), nullable=True),
    sa.Column("error_count", sa.Integer(), nullable=False, server_default="0"),
    sa.Column(
        "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
    sa.Column(
        "updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
    sa.UniqueConstraint("name", name="uq_worker_registry_name"),
)

# ── 20a. evaluation_reports ──────────────────────────────────────────────────
evaluation_reports = sa.Table(
    "evaluation_reports",
    metadata,
    sa.Column("id", sa.UUID(), primary_key=True),
    sa.Column(
        "worker_id",
        sa.UUID(),
        sa.ForeignKey("worker_registry.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column(
        "evaluated_at",
        sa.TIMESTAMP(timezone=True),
        server_default=sa.text("now()"),
        nullable=False,
    ),
    sa.Column("checks", JSONB(), nullable=False, server_default="{}"),
    sa.Column("overall_score", sa.Float(), nullable=True),
    sa.Column("verdict", sa.Text(), nullable=False, server_default="'PENDING'"),
    sa.Column("evaluator_version", sa.Text(), nullable=True),
    sa.Column("risk_tier", sa.Text(), nullable=False, server_default="'unknown'"),
    sa.Column("blocked_reasons", JSONB(), nullable=False, server_default="[]"),
    sa.Column("recommended_status", sa.Text(), nullable=False, server_default="'PENDING_EVALUATION'"),
    sa.Column("requires_human_approval", sa.Boolean(), nullable=False, server_default="false"),
    sa.Column("notes", sa.Text(), nullable=True),
)

# Project-scoped usage ledger.  Prometheus remains the fleet-level metrics
# surface; this durable table is the authority for per-project cost and call
# accounting shown in the workspace UI.
project_usage_events = sa.Table(
    "project_usage_events",
    metadata,
    sa.Column("id", sa.UUID(), primary_key=True),
    sa.Column(
        "project_id", sa.UUID(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
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
        "occurred_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
    sa.CheckConstraint(
        "event_type IN ('llm', 'tool')", name="ck_project_usage_events_event_type"
    ),
)

# ── 20b. Governed worker architecture ───────────────────────────────────────
# These tables deliberately keep immutable version/evidence records separate
# from the mutable registry row. Active runs therefore retain the exact shell,
# adapter, steward, and model resolution used at dispatch time.

worker_shell_versions = sa.Table(
    "worker_shell_versions",
    metadata,
    sa.Column("id", sa.UUID(), primary_key=True),
    sa.Column("worker_id", sa.UUID(), sa.ForeignKey("worker_registry.id", ondelete="CASCADE"), nullable=False),
    sa.Column("version", sa.Text(), nullable=False),
    sa.Column("contract_version", sa.Text(), nullable=False),
    sa.Column("schema_version", sa.Text(), nullable=False),
    sa.Column("identity_json", JSONB(), nullable=False, server_default="{}"),
    sa.Column("capabilities_json", JSONB(), nullable=False, server_default="{}"),
    sa.Column("permissions_json", JSONB(), nullable=False, server_default="{}"),
    sa.Column("model_mode", sa.Text(), nullable=False, server_default="none"),
    sa.Column("provenance_json", JSONB(), nullable=False, server_default="{}"),
    sa.Column("status", sa.Text(), nullable=False, server_default="active"),
    sa.Column("content_hash", sa.Text(), nullable=False),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
    sa.UniqueConstraint("worker_id", "version", name="uq_worker_shell_version"),
)

runtime_adapters = sa.Table(
    "runtime_adapters",
    metadata,
    sa.Column("id", sa.UUID(), primary_key=True),
    sa.Column("worker_id", sa.UUID(), sa.ForeignKey("worker_registry.id", ondelete="CASCADE"), nullable=False),
    sa.Column("version", sa.Text(), nullable=False),
    sa.Column("adapter_type", sa.Text(), nullable=False),
    sa.Column("transport_type", sa.Text(), nullable=False),
    sa.Column("runtime_api_version", sa.Text()),
    sa.Column("implementation_ref", sa.Text()),
    sa.Column("content_hash", sa.Text(), nullable=False),
    sa.Column("capabilities_json", JSONB(), nullable=False, server_default="{}"),
    sa.Column("conformance_status", sa.Text(), nullable=False, server_default="pending"),
    sa.Column("conformance_json", JSONB()),
    sa.Column("status", sa.Text(), nullable=False, server_default="candidate"),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
    sa.UniqueConstraint("worker_id", "version", name="uq_runtime_adapter_version"),
)

external_runtime_provenance = sa.Table(
    "external_runtime_provenance",
    metadata,
    sa.Column("id", sa.UUID(), primary_key=True),
    sa.Column("worker_id", sa.UUID(), sa.ForeignKey("worker_registry.id", ondelete="CASCADE"), nullable=False),
    sa.Column("canonical_source_repository", sa.Text(), nullable=False),
    sa.Column("source_provider", sa.Text(), nullable=False),
    sa.Column("exact_release", sa.Text()),
    sa.Column("commit_sha", sa.Text()),
    sa.Column("package_version", sa.Text()),
    sa.Column("oci_image_digest", sa.Text()),
    sa.Column("dependency_lock_hash", sa.Text()),
    sa.Column("protocol_api_version", sa.Text()),
    sa.Column("adapter_version", sa.Text()),
    sa.Column("transport_type", sa.Text(), nullable=False),
    sa.Column("runtime_fingerprint", sa.Text()),
    sa.Column("license_id", sa.Text()),
    sa.Column("redistribution_status", sa.Text(), nullable=False, server_default="pending"),
    sa.Column("security_scan_status", sa.Text(), nullable=False, server_default="pending"),
    sa.Column("documentation_snapshot_version", sa.Text()),
    sa.Column("last_verified_documentation_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("status", sa.Text(), nullable=False, server_default="candidate"),
    sa.Column("provenance_hash", sa.Text(), nullable=False),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
    sa.UniqueConstraint("worker_id", name="uq_external_provenance_worker"),
)

steward_agents = sa.Table(
    "steward_agents",
    metadata,
    sa.Column("id", sa.UUID(), primary_key=True),
    sa.Column("worker_id", sa.UUID(), sa.ForeignKey("worker_registry.id", ondelete="CASCADE"), nullable=False),
    sa.Column("status", sa.Text(), nullable=False, server_default="PROVISIONING"),
    sa.Column("steward_version", sa.Text(), nullable=False, server_default="1.0.0"),
    sa.Column("provenance_id", sa.UUID(), sa.ForeignKey("external_runtime_provenance.id", ondelete="RESTRICT")),
    sa.Column("active_skill_bundle_id", sa.UUID()),
    sa.Column("active_adapter_id", sa.UUID()),
    sa.Column("monitoring_cadence", sa.Text(), nullable=False, server_default="daily"),
    sa.Column("last_monitor_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("metadata", JSONB(), nullable=False, server_default="{}"),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
    sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
    sa.UniqueConstraint("worker_id", name="uq_steward_worker"),
)

steward_transitions = sa.Table(
    "steward_transitions",
    metadata,
    sa.Column("id", sa.UUID(), primary_key=True),
    sa.Column("steward_id", sa.UUID(), sa.ForeignKey("steward_agents.id", ondelete="CASCADE"), nullable=False),
    sa.Column("from_status", sa.Text(), nullable=False),
    sa.Column("to_status", sa.Text(), nullable=False),
    sa.Column("actor", sa.Text(), nullable=False),
    sa.Column("reason", sa.Text()),
    sa.Column("correlation_id", sa.Text()),
    sa.Column("evidence", JSONB(), nullable=False, server_default="{}"),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
)

documentation_sources = sa.Table(
    "documentation_sources",
    metadata,
    sa.Column("id", sa.UUID(), primary_key=True),
    sa.Column("steward_id", sa.UUID(), sa.ForeignKey("steward_agents.id", ondelete="CASCADE"), nullable=False),
    sa.Column("uri", sa.Text(), nullable=False),
    sa.Column("source_type", sa.Text(), nullable=False, server_default="official"),
    sa.Column("trusted_for_provenance", sa.Boolean(), nullable=False, server_default="false"),
    sa.Column("allowed_domains", sa.ARRAY(sa.Text()), nullable=False, server_default="{}"),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
)

documentation_snapshots = sa.Table(
    "documentation_snapshots",
    metadata,
    sa.Column("id", sa.UUID(), primary_key=True),
    sa.Column("source_id", sa.UUID(), sa.ForeignKey("documentation_sources.id", ondelete="CASCADE"), nullable=False),
    sa.Column("version", sa.Text(), nullable=False),
    sa.Column("content_sha256", sa.Text(), nullable=False),
    sa.Column("content_ref", sa.Text()),
    sa.Column("extracted_interfaces", JSONB(), nullable=False, server_default="{}"),
    sa.Column("security_findings", sa.ARRAY(sa.Text()), nullable=False, server_default="{}"),
    sa.Column("untrusted", sa.Boolean(), nullable=False, server_default="true"),
    sa.Column("captured_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
    sa.UniqueConstraint("source_id", "version", name="uq_documentation_snapshot_version"),
)

capability_snapshots = sa.Table(
    "capability_snapshots",
    metadata,
    sa.Column("id", sa.UUID(), primary_key=True),
    sa.Column("worker_id", sa.UUID(), sa.ForeignKey("worker_registry.id", ondelete="CASCADE"), nullable=False),
    sa.Column("steward_id", sa.UUID(), sa.ForeignKey("steward_agents.id", ondelete="SET NULL")),
    sa.Column("version", sa.Text(), nullable=False),
    sa.Column("capabilities_json", JSONB(), nullable=False),
    sa.Column("evidence_refs", sa.ARRAY(sa.Text()), nullable=False, server_default="{}"),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
)

compatibility_matrices = sa.Table(
    "compatibility_matrices",
    metadata,
    sa.Column("id", sa.UUID(), primary_key=True),
    sa.Column("worker_id", sa.UUID(), sa.ForeignKey("worker_registry.id", ondelete="CASCADE"), nullable=False),
    sa.Column("runtime_version", sa.Text(), nullable=False),
    sa.Column("adapter_version", sa.Text(), nullable=False),
    sa.Column("contract_version", sa.Text(), nullable=False),
    sa.Column("model_profiles_json", JSONB(), nullable=False, server_default="{}"),
    sa.Column("capabilities_json", JSONB(), nullable=False, server_default="{}"),
    sa.Column("fixtures", sa.ARRAY(sa.Text()), nullable=False, server_default="{}"),
    sa.Column("passed", sa.Boolean(), nullable=False, server_default="false"),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
)

certification_runs = sa.Table(
    "certification_runs",
    metadata,
    sa.Column("id", sa.UUID(), primary_key=True),
    sa.Column("worker_id", sa.UUID(), sa.ForeignKey("worker_registry.id", ondelete="CASCADE"), nullable=False),
    sa.Column("steward_id", sa.UUID(), sa.ForeignKey("steward_agents.id", ondelete="SET NULL")),
    sa.Column("candidate_id", sa.UUID()),
    sa.Column("status", sa.Text(), nullable=False, server_default="running"),
    sa.Column("conformance_json", JSONB(), nullable=False, server_default="{}"),
    sa.Column("checks_json", JSONB(), nullable=False, server_default="{}"),
    sa.Column("evidence_json", JSONB(), nullable=False, server_default="{}"),
    sa.Column("failure_reasons", sa.ARRAY(sa.Text()), nullable=False, server_default="{}"),
    sa.Column("started_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
    sa.Column("completed_at", sa.TIMESTAMP(timezone=True)),
)

# ── PM integration control plane ─────────────────────────────────────────────
# These tables record provider configuration and delivery state only.  The
# orchestrator remains the sole writer of canonical projects/sprints/issues.
pm_connections = sa.Table(
    "pm_connections",
    metadata,
    sa.Column("id", sa.UUID(), primary_key=True),
    sa.Column("provider_kind", sa.Text(), nullable=False),
    sa.Column("display_name", sa.Text(), nullable=False),
    sa.Column("base_url", sa.Text(), nullable=False),
    sa.Column("credential_ref", sa.Text(), nullable=False),
    sa.Column("capability_profile", sa.Text(), nullable=False, server_default="pm"),
    sa.Column("config", JSONB(), nullable=False, server_default="{}"),
    sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
    sa.Column("status", sa.Text(), nullable=False, server_default="DISABLED"),
    sa.Column("revision", sa.BigInteger(), nullable=False, server_default="1"),
    sa.Column("created_by", sa.Text(), nullable=False),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
    sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
    sa.Column("last_health_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("last_health_status", sa.Text()),
    sa.Column("last_health_error", sa.Text()),
)

pm_project_bindings = sa.Table(
    "pm_project_bindings",
    metadata,
    sa.Column("id", sa.UUID(), primary_key=True),
    sa.Column("project_id", sa.UUID(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
    sa.Column("connection_id", sa.UUID(), sa.ForeignKey("pm_connections.id", ondelete="CASCADE"), nullable=False),
    sa.Column("external_project_id", sa.Text()),
    sa.Column("external_project_key", sa.Text()),
    sa.Column("external_repository", sa.Text()),
    sa.Column("mapping_profile", sa.Text(), nullable=False, server_default="default"),
    sa.Column("direction", sa.Text(), nullable=False, server_default="outbound"),
    sa.Column("sync_cursor", sa.Text()),
    sa.Column("status", sa.Text(), nullable=False, server_default="DISABLED"),
    sa.Column("revision", sa.BigInteger(), nullable=False, server_default="1"),
    sa.Column("last_reconciled_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("provisioning_state", sa.Text(), nullable=False, server_default="UNPROVISIONED"),
    sa.Column("provisioning_plan_id", sa.UUID()),
    sa.Column("provisioning_plan_digest", sa.Text()),
    sa.Column("activation_blockers", JSONB(), nullable=False, server_default="[]"),
    sa.Column("webhook_verified_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("projection_verified_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("reconciliation_verified_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("webhook_events", JSONB(), nullable=False, server_default="[]"),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
    sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
)

pm_object_mappings = sa.Table(
    "pm_object_mappings",
    metadata,
    sa.Column("id", sa.UUID(), primary_key=True),
    sa.Column("connection_id", sa.UUID(), sa.ForeignKey("pm_connections.id", ondelete="CASCADE"), nullable=False),
    sa.Column("object_type", sa.Text(), nullable=False),
    sa.Column("aiat_object_id", sa.UUID(), nullable=False),
    sa.Column("external_id", sa.Text(), nullable=False),
    sa.Column("external_key", sa.Text()),
    sa.Column("provider_version", sa.Text()),
    sa.Column("content_hash", sa.Text()),
    sa.Column("last_import_revision", sa.BigInteger()),
    sa.Column("last_export_revision", sa.BigInteger()),
    sa.Column("last_imported_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("last_exported_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("metadata", JSONB(), nullable=False, server_default="{}"),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
    sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
    sa.UniqueConstraint("connection_id", "object_type", "aiat_object_id", name="uq_pm_mapping_aiat"),
    sa.UniqueConstraint("connection_id", "object_type", "external_id", name="uq_pm_mapping_external"),
)

pm_inbox_events = sa.Table(
    "pm_inbox_events",
    metadata,
    sa.Column("id", sa.UUID(), primary_key=True),
    sa.Column("connection_id", sa.UUID(), sa.ForeignKey("pm_connections.id", ondelete="CASCADE"), nullable=False),
    sa.Column("provider_delivery_id", sa.Text(), nullable=False),
    sa.Column("event_type", sa.Text(), nullable=False),
    sa.Column("payload", JSONB(), nullable=False),
    sa.Column("raw_body", sa.LargeBinary()),
    sa.Column("headers", JSONB(), nullable=False, server_default="{}"),
    sa.Column("payload_hash", sa.Text(), nullable=False),
    sa.Column("verified", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    sa.Column("status", sa.Text(), nullable=False, server_default="RECEIVED"),
    sa.Column("normalized_type", sa.Text()),
    sa.Column("result", JSONB()),
    sa.Column("error", sa.Text()),
    sa.Column("correlation_id", sa.Text()),
    sa.Column("causation_id", sa.Text()),
    sa.Column("received_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
    sa.Column("processed_at", sa.TIMESTAMP(timezone=True)),
    sa.UniqueConstraint("connection_id", "provider_delivery_id", name="uq_pm_inbox_delivery"),
)

pm_outbox_events = sa.Table(
    "pm_outbox_events",
    metadata,
    sa.Column("id", sa.UUID(), primary_key=True),
    sa.Column("connection_id", sa.UUID(), sa.ForeignKey("pm_connections.id", ondelete="CASCADE"), nullable=False),
    sa.Column("aggregate_type", sa.Text(), nullable=False),
    sa.Column("aggregate_id", sa.UUID(), nullable=False),
    sa.Column("canonical_revision", sa.BigInteger(), nullable=False),
    sa.Column("operation", sa.Text(), nullable=False),
    sa.Column("idempotency_key", sa.Text(), nullable=False),
    sa.Column("payload", JSONB(), nullable=False),
    sa.Column("status", sa.Text(), nullable=False, server_default="PENDING"),
    sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("claimed_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("next_attempt_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
    sa.Column("last_error", sa.Text()),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
    sa.Column("processed_at", sa.TIMESTAMP(timezone=True)),
    sa.UniqueConstraint("idempotency_key", name="uq_pm_outbox_idempotency"),
)

pm_delivery_attempts = sa.Table(
    "pm_delivery_attempts",
    metadata,
    sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
    sa.Column("outbox_id", sa.UUID(), sa.ForeignKey("pm_outbox_events.id", ondelete="CASCADE"), nullable=False),
    sa.Column("attempt", sa.Integer(), nullable=False),
    sa.Column("status", sa.Text(), nullable=False),
    sa.Column("provider_status", sa.Integer()),
    sa.Column("response_metadata", JSONB(), nullable=False, server_default="{}"),
    sa.Column("error", sa.Text()),
    sa.Column("attempted_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
)

pm_outbox_dispositions = sa.Table(
    "pm_outbox_dispositions",
    metadata,
    sa.Column("id", sa.UUID(), primary_key=True),
    sa.Column("outbox_id", sa.UUID(), sa.ForeignKey("pm_outbox_events.id", ondelete="CASCADE"), nullable=False),
    sa.Column("connection_id", sa.UUID(), sa.ForeignKey("pm_connections.id", ondelete="CASCADE"), nullable=False),
    sa.Column("binding_id", sa.UUID(), sa.ForeignKey("pm_project_bindings.id", ondelete="SET NULL")),
    sa.Column("disposition", sa.Text(), nullable=False),
    sa.Column("reason", sa.Text(), nullable=False),
    sa.Column("actor", sa.Text(), nullable=False),
    sa.Column("provider_state", JSONB(), nullable=False, server_default="{}"),
    sa.Column("evidence_id", sa.UUID(), sa.ForeignKey("integration_evidence_records.id", ondelete="RESTRICT"), nullable=False),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    sa.UniqueConstraint("outbox_id", name="uq_pm_outbox_disposition"),
    sa.CheckConstraint("disposition IN ('RESOLVED', 'SUPERSEDED')", name="ck_pm_outbox_disposition_kind"),
)

pm_conflicts = sa.Table(
    "pm_conflicts",
    metadata,
    sa.Column("id", sa.UUID(), primary_key=True),
    sa.Column("connection_id", sa.UUID(), sa.ForeignKey("pm_connections.id", ondelete="CASCADE"), nullable=False),
    sa.Column("binding_id", sa.UUID(), sa.ForeignKey("pm_project_bindings.id", ondelete="CASCADE")),
    sa.Column("object_type", sa.Text(), nullable=False),
    sa.Column("aiat_object_id", sa.UUID()),
    sa.Column("external_id", sa.Text()),
    sa.Column("reason", sa.Text(), nullable=False),
    sa.Column("canonical_snapshot", JSONB()),
    sa.Column("external_snapshot", JSONB()),
    sa.Column("status", sa.Text(), nullable=False, server_default="OPEN"),
    sa.Column("resolution", JSONB()),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
    sa.Column("resolved_at", sa.TIMESTAMP(timezone=True)),
)

pm_reconciliation_runs = sa.Table(
    "pm_reconciliation_runs",
    metadata,
    sa.Column("id", sa.UUID(), primary_key=True),
    sa.Column("connection_id", sa.UUID(), sa.ForeignKey("pm_connections.id", ondelete="CASCADE"), nullable=False),
    sa.Column("binding_id", sa.UUID(), sa.ForeignKey("pm_project_bindings.id", ondelete="SET NULL")),
    sa.Column("mode", sa.Text(), nullable=False, server_default="audit"),
    sa.Column("status", sa.Text(), nullable=False, server_default="RUNNING"),
    sa.Column("cursor", sa.Text()),
    sa.Column("next_cursor", sa.Text()),
    sa.Column("counts", JSONB(), nullable=False, server_default="{}"),
    sa.Column("error", sa.Text()),
    sa.Column("started_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
    sa.Column("completed_at", sa.TIMESTAMP(timezone=True)),
)

pm_cutovers = sa.Table(
    "pm_cutovers",
    metadata,
    sa.Column("id", sa.UUID(), primary_key=True),
    sa.Column("project_id", sa.UUID(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
    sa.Column("from_binding_id", sa.UUID(), sa.ForeignKey("pm_project_bindings.id", ondelete="SET NULL")),
    sa.Column("to_binding_id", sa.UUID(), sa.ForeignKey("pm_project_bindings.id", ondelete="SET NULL"), nullable=False),
    sa.Column("status", sa.Text(), nullable=False, server_default="RUNNING"),
    sa.Column("confirmation", JSONB(), nullable=False, server_default="{}"),
    sa.Column("rollback_ready", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    sa.Column("error", sa.Text()),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
    sa.Column("completed_at", sa.TIMESTAMP(timezone=True)),
)

pm_lifecycle_plans = sa.Table(
    "pm_lifecycle_plans",
    metadata,
    sa.Column("id", sa.UUID(), primary_key=True),
    sa.Column("plan_kind", sa.Text(), nullable=False),
    sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
    sa.Column("target_type", sa.Text(), nullable=False),
    sa.Column("target_id", sa.UUID(), nullable=False),
    sa.Column("connection_id", sa.UUID(), sa.ForeignKey("pm_connections.id", ondelete="CASCADE"), nullable=False),
    sa.Column("binding_id", sa.UUID(), sa.ForeignKey("pm_project_bindings.id", ondelete="CASCADE")),
    sa.Column("expected_connection_status", sa.Text()),
    sa.Column("expected_binding_status", sa.Text()),
    sa.Column("expected_connection_revision", sa.BigInteger()),
    sa.Column("expected_binding_revision", sa.BigInteger()),
    sa.Column("desired_connection_status", sa.Text()),
    sa.Column("desired_binding_status", sa.Text()),
    sa.Column("observed_versions", JSONB(), nullable=False, server_default="{}"),
    sa.Column("operations", JSONB(), nullable=False, server_default="[]"),
    sa.Column("gate_results", JSONB(), nullable=False, server_default="{}"),
    sa.Column("evidence_refs", JSONB(), nullable=False, server_default="{}"),
    sa.Column("blockers", JSONB(), nullable=False, server_default="[]"),
    sa.Column("rollback_operations", JSONB(), nullable=False, server_default="[]"),
    sa.Column("created_by", sa.Text(), nullable=False),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("digest", sa.Text(), nullable=False),
    sa.Column("status", sa.Text(), nullable=False, server_default="PLANNED"),
    sa.Column("approval_actor", sa.Text()),
    sa.Column("approved_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("approval_reason", sa.Text()),
    sa.Column("applied_actor", sa.Text()),
    sa.Column("applied_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("application_result", JSONB()),
    sa.Column("error", sa.Text()),
    sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    sa.UniqueConstraint("digest", name="uq_pm_lifecycle_plan_digest"),
)

pm_lifecycle_audits = sa.Table(
    "pm_lifecycle_audits",
    metadata,
    sa.Column("id", sa.UUID(), primary_key=True),
    sa.Column("plan_id", sa.UUID(), sa.ForeignKey("pm_lifecycle_plans.id", ondelete="RESTRICT"), nullable=False),
    sa.Column("connection_id", sa.UUID(), sa.ForeignKey("pm_connections.id", ondelete="CASCADE"), nullable=False),
    sa.Column("binding_id", sa.UUID(), sa.ForeignKey("pm_project_bindings.id", ondelete="SET NULL")),
    sa.Column("action", sa.Text(), nullable=False),
    sa.Column("before_state", JSONB(), nullable=False),
    sa.Column("after_state", JSONB(), nullable=False),
    sa.Column("actor", sa.Text(), nullable=False),
    sa.Column("approval_reference", JSONB(), nullable=False, server_default="{}"),
    sa.Column("evidence_refs", JSONB(), nullable=False, server_default="{}"),
    sa.Column("transaction_id", sa.Text(), nullable=False),
    sa.Column("rollback_operations", JSONB(), nullable=False, server_default="[]"),
    sa.Column("occurred_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
)

# Immutable provider identities are kept outside connection configuration so a
# human authorization cannot be forged by editing JSON metadata.  The mapping
# key is the provider's immutable actor ID, scoped to one connection/tenant.
pm_external_actor_mappings = sa.Table(
    "pm_external_actor_mappings",
    metadata,
    sa.Column("id", sa.UUID(), primary_key=True),
    sa.Column("connection_id", sa.UUID(), sa.ForeignKey("pm_connections.id", ondelete="CASCADE"), nullable=False),
    sa.Column("provider_kind", sa.Text(), nullable=False),
    sa.Column("tenant_key", sa.Text(), nullable=False),
    sa.Column("external_actor_id", sa.Text(), nullable=False),
    sa.Column("actor_snapshot", JSONB(), nullable=False, server_default="{}"),
    sa.Column("aiat_identity_id", sa.Text(), nullable=False),
    sa.Column("status", sa.Text(), nullable=False, server_default="TRUSTED"),
    sa.Column("authorized_scopes", JSONB(), nullable=False, server_default="[]"),
    sa.Column("created_by", sa.Text(), nullable=False),
    sa.Column("approved_by", sa.Text(), nullable=False),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    sa.Column("approved_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    sa.Column("revoked_by", sa.Text()),
    sa.Column("revoked_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("revocation_reason", sa.Text()),
    sa.Column("revision", sa.BigInteger(), nullable=False, server_default="1"),
    sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    sa.UniqueConstraint("connection_id", "tenant_key", "external_actor_id", name="uq_pm_external_actor_connection"),
)

pm_external_actor_mapping_audits = sa.Table(
    "pm_external_actor_mapping_audits",
    metadata,
    sa.Column("id", sa.UUID(), primary_key=True),
    sa.Column("mapping_id", sa.UUID(), sa.ForeignKey("pm_external_actor_mappings.id", ondelete="RESTRICT"), nullable=False),
    sa.Column("action", sa.Text(), nullable=False),
    sa.Column("actor", sa.Text(), nullable=False),
    sa.Column("before_state", JSONB(), nullable=False, server_default="{}"),
    sa.Column("after_state", JSONB(), nullable=False, server_default="{}"),
    sa.Column("evidence_refs", JSONB(), nullable=False, server_default="{}"),
    sa.Column("occurred_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
)

# A canary never widens a binding to ACTIVE.  It is an independently scoped,
# short-lived authorization to accept one exact inbound command while the
# binding remains READ_ONLY.
pm_inbound_canary_plans = sa.Table(
    "pm_inbound_canary_plans",
    metadata,
    sa.Column("id", sa.UUID(), primary_key=True),
    sa.Column("connection_id", sa.UUID(), sa.ForeignKey("pm_connections.id", ondelete="CASCADE"), nullable=False),
    sa.Column("binding_id", sa.UUID(), sa.ForeignKey("pm_project_bindings.id", ondelete="CASCADE"), nullable=False),
    sa.Column("project_id", sa.UUID(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
    sa.Column("canonical_issue_id", sa.UUID(), sa.ForeignKey("issues.id", ondelete="CASCADE"), nullable=False),
    sa.Column("external_issue_id", sa.Text(), nullable=False),
    sa.Column("mapping_id", sa.UUID(), sa.ForeignKey("pm_object_mappings.id", ondelete="RESTRICT"), nullable=False),
    sa.Column("actor_mapping_id", sa.UUID(), sa.ForeignKey("pm_external_actor_mappings.id", ondelete="RESTRICT"), nullable=False),
    sa.Column("expected_connection_status", sa.Text(), nullable=False),
    sa.Column("expected_binding_status", sa.Text(), nullable=False),
    sa.Column("expected_connection_revision", sa.BigInteger(), nullable=False),
    sa.Column("expected_binding_revision", sa.BigInteger(), nullable=False),
    sa.Column("expected_canonical_revision", sa.BigInteger(), nullable=False),
    sa.Column("current_priority", sa.Text(), nullable=False),
    sa.Column("target_priority", sa.Text(), nullable=False),
    sa.Column("max_command_count", sa.Integer(), nullable=False, server_default="1"),
    sa.Column("accepted_command_count", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("operations", JSONB(), nullable=False, server_default="[]"),
    sa.Column("gate_results", JSONB(), nullable=False, server_default="{}"),
    sa.Column("evidence_refs", JSONB(), nullable=False, server_default="{}"),
    sa.Column("rollback_operations", JSONB(), nullable=False, server_default="[]"),
    sa.Column("created_by", sa.Text(), nullable=False),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("digest", sa.Text(), nullable=False, unique=True),
    sa.Column("status", sa.Text(), nullable=False, server_default="PLANNED"),
    sa.Column("approved_by", sa.Text()),
    sa.Column("approved_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("armed_by", sa.Text()),
    sa.Column("armed_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("expired_by", sa.Text()),
    sa.Column("expired_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("completed_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("result", JSONB()),
    sa.Column("error", sa.Text()),
    sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
)

work_item_comments = sa.Table(
    "work_item_comments",
    metadata,
    sa.Column("id", sa.UUID(), primary_key=True),
    sa.Column("issue_id", sa.UUID(), sa.ForeignKey("issues.id", ondelete="CASCADE"), nullable=False),
    sa.Column("body", sa.Text(), nullable=False),
    sa.Column("actor_id", sa.Text(), nullable=False),
    sa.Column("run_id", sa.UUID()),
    sa.Column("approval_id", sa.UUID()),
    sa.Column("evidence_id", sa.Text()),
    sa.Column("body_blob_ref", sa.Text()),
    sa.Column("origin", sa.Text(), nullable=False, server_default="aiat"),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
)

work_item_links = sa.Table(
    "work_item_links",
    metadata,
    sa.Column("id", sa.UUID(), primary_key=True),
    sa.Column("issue_id", sa.UUID(), sa.ForeignKey("issues.id", ondelete="CASCADE"), nullable=False),
    sa.Column("link_type", sa.Text(), nullable=False),
    sa.Column("target_type", sa.Text(), nullable=False),
    sa.Column("target_id", sa.Text(), nullable=False),
    sa.Column("metadata", JSONB(), nullable=False, server_default="{}"),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    sa.UniqueConstraint("issue_id", "link_type", "target_type", "target_id", name="uq_work_item_link"),
)

integration_evidence_records = sa.Table(
    "integration_evidence_records",
    metadata,
    sa.Column("id", sa.UUID(), primary_key=True),
    sa.Column("connection_id", sa.UUID(), sa.ForeignKey("pm_connections.id", ondelete="CASCADE"), nullable=False),
    sa.Column("binding_id", sa.UUID(), sa.ForeignKey("pm_project_bindings.id", ondelete="SET NULL")),
    sa.Column("project_id", sa.UUID(), sa.ForeignKey("projects.id", ondelete="SET NULL")),
    sa.Column("evidence_type", sa.Text(), nullable=False),
    sa.Column("external_id", sa.Text()),
    sa.Column("repository", sa.Text()),
    sa.Column("payload", JSONB(), nullable=False, server_default="{}"),
    sa.Column("idempotency_key", sa.Text(), nullable=False),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    sa.UniqueConstraint("idempotency_key", name="uq_integration_evidence_idempotency"),
)

skill_bundles = sa.Table(
    "skill_bundles",
    metadata,
    sa.Column("id", sa.UUID(), primary_key=True),
    sa.Column("steward_id", sa.UUID(), sa.ForeignKey("steward_agents.id", ondelete="CASCADE"), nullable=False),
    sa.Column("worker_id", sa.UUID(), sa.ForeignKey("worker_registry.id", ondelete="CASCADE"), nullable=False),
    sa.Column("semantic_version", sa.Text(), nullable=False),
    sa.Column("format_version", sa.Text(), nullable=False),
    sa.Column("upstream_compatibility_range", sa.Text(), nullable=False),
    sa.Column("provenance_json", JSONB(), nullable=False),
    sa.Column("bundle_json", JSONB(), nullable=False),
    sa.Column("content_hash", sa.Text(), nullable=False),
    sa.Column("status", sa.Text(), nullable=False, server_default="DRAFT"),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
    sa.UniqueConstraint("steward_id", "semantic_version", name="uq_skill_bundle_version"),
)

skill_bundle_candidates = sa.Table(
    "skill_bundle_candidates",
    metadata,
    sa.Column("id", sa.UUID(), primary_key=True),
    sa.Column("skill_bundle_id", sa.UUID(), sa.ForeignKey("skill_bundles.id", ondelete="RESTRICT"), nullable=False),
    sa.Column("adapter_id", sa.UUID(), sa.ForeignKey("runtime_adapters.id", ondelete="RESTRICT")),
    sa.Column("worker_id", sa.UUID(), sa.ForeignKey("worker_registry.id", ondelete="CASCADE"), nullable=False),
    sa.Column("intake_status", sa.Text(), nullable=False, server_default="DISCOVERED"),
    sa.Column("diff_json", JSONB(), nullable=False, server_default="{}"),
    sa.Column("evidence_json", JSONB(), nullable=False, server_default="{}"),
    sa.Column("certification_run_id", sa.UUID(), sa.ForeignKey("certification_runs.id", ondelete="SET NULL")),
    sa.Column("approval_record_id", sa.UUID()),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
)

model_profiles = sa.Table(
    "model_profiles",
    metadata,
    sa.Column("id", sa.UUID(), primary_key=True),
    sa.Column("logical_profile_id", sa.Text(), nullable=False),
    sa.Column("purpose", sa.Text(), nullable=False),
    sa.Column("approved_provider_ids", sa.ARRAY(sa.Text()), nullable=False, server_default="{}"),
    sa.Column("required_capabilities", sa.ARRAY(sa.Text()), nullable=False, server_default="{}"),
    sa.Column("fallback_profile_ids", sa.ARRAY(sa.Text()), nullable=False, server_default="{}"),
    sa.Column("status", sa.Text(), nullable=False, server_default="draft"),
    sa.Column("owner", sa.Text(), nullable=False, server_default="aiat"),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
    sa.UniqueConstraint("logical_profile_id", name="uq_model_profile_logical_id"),
)

model_profile_versions = sa.Table(
    "model_profile_versions",
    metadata,
    sa.Column("id", sa.UUID(), primary_key=True),
    sa.Column("profile_id", sa.UUID(), sa.ForeignKey("model_profiles.id", ondelete="CASCADE"), nullable=False),
    sa.Column("version", sa.Text(), nullable=False),
    sa.Column("provider_id", sa.Text(), nullable=False),
    sa.Column("exact_model_id", sa.Text(), nullable=False),
    sa.Column("api_version", sa.Text()),
    sa.Column("capabilities", sa.ARRAY(sa.Text()), nullable=False, server_default="{}"),
    sa.Column("constraints_json", JSONB(), nullable=False, server_default="{}"),
    sa.Column("provider_settings", JSONB(), nullable=False, server_default="{}"),
    sa.Column("status", sa.Text(), nullable=False, server_default="draft"),
    sa.Column("effective_from", sa.TIMESTAMP(timezone=True)),
    sa.Column("effective_until", sa.TIMESTAMP(timezone=True)),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
    sa.UniqueConstraint("profile_id", "version", name="uq_model_profile_version"),
)

model_resolution_snapshots = sa.Table(
    "model_resolution_snapshots",
    metadata,
    sa.Column("id", sa.UUID(), primary_key=True),
    sa.Column("project_id", sa.UUID(), sa.ForeignKey("projects.id", ondelete="SET NULL")),
    sa.Column("requested_profile_id", sa.Text()),
    sa.Column("resolved_profile_id", sa.Text()),
    sa.Column("resolved_profile_version", sa.Text()),
    sa.Column("provider_id", sa.Text()),
    sa.Column("exact_model_id", sa.Text()),
    sa.Column("effective_constraints", JSONB(), nullable=False, server_default="{}"),
    sa.Column("effective_configuration", JSONB(), nullable=False, server_default="{}"),
    sa.Column("capability_checks", JSONB(), nullable=False, server_default="{}"),
    sa.Column("rejected_candidates", JSONB(), nullable=False, server_default="[]"),
    sa.Column("fallback_chain", sa.ARRAY(sa.Text()), nullable=False, server_default="{}"),
    sa.Column("cost_estimate_usd", sa.Numeric(14, 8), nullable=False, server_default="0"),
    sa.Column("override_approval_id", sa.UUID()),
    sa.Column("selection_reason", sa.Text()),
    sa.Column("policy_failure_code", sa.Text()),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
)

model_override_requests = sa.Table(
    "model_override_requests",
    metadata,
    sa.Column("id", sa.UUID(), primary_key=True),
    sa.Column("project_id", sa.UUID(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
    sa.Column("requested_by", sa.Text(), nullable=False),
    sa.Column("requested_profile_id", sa.Text(), nullable=False),
    sa.Column("reason", sa.Text(), nullable=False),
    sa.Column("scope", JSONB(), nullable=False, server_default="{}"),
    sa.Column("status", sa.Text(), nullable=False, server_default="PENDING"),
    sa.Column("decided_by", sa.Text()),
    sa.Column("decision", sa.Text()),
    sa.Column("expires_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
    sa.Column("decided_at", sa.TIMESTAMP(timezone=True)),
)

rollout_records = sa.Table(
    "rollout_records",
    metadata,
    sa.Column("id", sa.UUID(), primary_key=True),
    sa.Column("worker_id", sa.UUID(), sa.ForeignKey("worker_registry.id", ondelete="CASCADE"), nullable=False),
    sa.Column("steward_id", sa.UUID(), sa.ForeignKey("steward_agents.id", ondelete="RESTRICT"), nullable=False),
    sa.Column("candidate_id", sa.UUID(), sa.ForeignKey("skill_bundle_candidates.id", ondelete="RESTRICT"), nullable=False),
    sa.Column("status", sa.Text(), nullable=False, server_default="PENDING"),
    sa.Column("eligible_task_classes", sa.ARRAY(sa.Text()), nullable=False, server_default="{}"),
    sa.Column("sample_count", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("sample_targets", JSONB(), nullable=False, server_default="{}"),
    sa.Column("comparison_metrics", JSONB(), nullable=False, server_default="{}"),
    sa.Column("rollback_thresholds", JSONB(), nullable=False, server_default="{}"),
    sa.Column("in_flight_policy", sa.Text(), nullable=False, server_default="finish_pinned_version"),
    sa.Column("promotion_actor", sa.Text()),
    sa.Column("rollback_reason", sa.Text()),
    sa.Column("started_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
    sa.Column("completed_at", sa.TIMESTAMP(timezone=True)),
    sa.UniqueConstraint("worker_id", "candidate_id", name="uq_rollout_worker_candidate"),
)

rollout_transitions = sa.Table(
    "rollout_transitions",
    metadata,
    sa.Column("id", sa.UUID(), primary_key=True),
    sa.Column("rollout_id", sa.UUID(), sa.ForeignKey("rollout_records.id", ondelete="CASCADE"), nullable=False),
    sa.Column("from_status", sa.Text(), nullable=False),
    sa.Column("to_status", sa.Text(), nullable=False),
    sa.Column("actor", sa.Text(), nullable=False),
    sa.Column("reason", sa.Text()),
    sa.Column("evidence", JSONB(), nullable=False, server_default="{}"),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
)

rollback_records = sa.Table(
    "rollback_records",
    metadata,
    sa.Column("id", sa.UUID(), primary_key=True),
    sa.Column("rollout_id", sa.UUID(), sa.ForeignKey("rollout_records.id", ondelete="CASCADE"), nullable=False),
    sa.Column("worker_id", sa.UUID(), sa.ForeignKey("worker_registry.id", ondelete="CASCADE"), nullable=False),
    sa.Column("from_candidate_id", sa.UUID()),
    sa.Column("target_candidate_id", sa.UUID()),
    sa.Column("reason", sa.Text(), nullable=False),
    sa.Column("triggered_by", sa.Text(), nullable=False),
    sa.Column("evidence", JSONB(), nullable=False, server_default="{}"),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
)

worker_runs = sa.Table(
    "worker_runs",
    metadata,
    sa.Column("id", sa.UUID(), primary_key=True),
    sa.Column("idempotency_key", sa.Text(), nullable=False),
    sa.Column("worker_id", sa.UUID(), sa.ForeignKey("worker_registry.id", ondelete="RESTRICT"), nullable=False),
    sa.Column("project_id", sa.UUID(), sa.ForeignKey("projects.id", ondelete="SET NULL")),
    sa.Column("flow_id", sa.UUID(), sa.ForeignKey("flows.id", ondelete="SET NULL")),
    sa.Column("flow_instance_id", sa.UUID(), sa.ForeignKey("flow_instances.id", ondelete="SET NULL")),
    sa.Column("flow_node_execution_id", sa.BigInteger(), sa.ForeignKey("flow_node_executions.id", ondelete="SET NULL")),
    sa.Column("worker_shell_version_id", sa.UUID(), sa.ForeignKey("worker_shell_versions.id", ondelete="RESTRICT")),
    sa.Column("adapter_id", sa.UUID(), sa.ForeignKey("runtime_adapters.id", ondelete="RESTRICT")),
    sa.Column("steward_id", sa.UUID(), sa.ForeignKey("steward_agents.id", ondelete="SET NULL")),
    sa.Column("model_resolution_snapshot_id", sa.UUID(), sa.ForeignKey("model_resolution_snapshots.id", ondelete="SET NULL")),
    sa.Column("task_type", sa.Text(), nullable=False),
    sa.Column("state", sa.Text(), nullable=False, server_default="CREATED"),
    sa.Column("request_json", JSONB(), nullable=False),
    sa.Column("negotiation_json", JSONB(), nullable=False, server_default="{}"),
    sa.Column("result_json", JSONB()),
    sa.Column("error_json", JSONB()),
    sa.Column("replay_metadata", JSONB(), nullable=False, server_default="{}"),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
    sa.Column("started_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("completed_at", sa.TIMESTAMP(timezone=True)),
    sa.UniqueConstraint("worker_id", "idempotency_key", name="uq_worker_run_idempotency"),
)

worker_run_transitions = sa.Table(
    "worker_run_transitions",
    metadata,
    sa.Column("id", sa.UUID(), primary_key=True),
    sa.Column("run_id", sa.UUID(), sa.ForeignKey("worker_runs.id", ondelete="CASCADE"), nullable=False),
    sa.Column("from_state", sa.Text(), nullable=False),
    sa.Column("to_state", sa.Text(), nullable=False),
    sa.Column("actor", sa.Text(), nullable=False),
    sa.Column("reason", sa.Text()),
    sa.Column("correlation_id", sa.Text()),
    sa.Column("metadata", JSONB(), nullable=False, server_default="{}"),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
)

worker_events = sa.Table(
    "worker_events",
    metadata,
    sa.Column("id", sa.UUID(), primary_key=True),
    sa.Column("run_id", sa.UUID(), sa.ForeignKey("worker_runs.id", ondelete="CASCADE"), nullable=False),
    sa.Column("sequence", sa.Integer(), nullable=False),
    sa.Column("event_type", sa.Text(), nullable=False),
    sa.Column("event_json", JSONB(), nullable=False),
    sa.Column("event_sha256", sa.Text(), nullable=False),
    sa.Column("occurred_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
    sa.UniqueConstraint("run_id", "sequence", name="uq_worker_event_sequence"),
)

worker_checkpoints = sa.Table(
    "worker_checkpoints",
    metadata,
    sa.Column("id", sa.UUID(), primary_key=True),
    sa.Column("run_id", sa.UUID(), sa.ForeignKey("worker_runs.id", ondelete="CASCADE"), nullable=False),
    sa.Column("sequence", sa.Integer(), nullable=False),
    sa.Column("state_json", JSONB(), nullable=False),
    sa.Column("artifact_id", sa.BigInteger(), sa.ForeignKey("artifacts.id", ondelete="SET NULL")),
    sa.Column("resumable", sa.Boolean(), nullable=False, server_default="true"),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
    sa.UniqueConstraint("run_id", "sequence", name="uq_worker_checkpoint_sequence"),
)

worker_artifacts = sa.Table(
    "worker_artifacts",
    metadata,
    sa.Column("id", sa.UUID(), primary_key=True),
    sa.Column("run_id", sa.UUID(), sa.ForeignKey("worker_runs.id", ondelete="CASCADE"), nullable=False),
    sa.Column("artifact_id", sa.BigInteger(), sa.ForeignKey("artifacts.id", ondelete="RESTRICT")),
    sa.Column("kind", sa.Text(), nullable=False, server_default="other"),
    sa.Column("uri", sa.Text(), nullable=False),
    sa.Column("sha256", sa.Text(), nullable=False),
    sa.Column("size_bytes", sa.BigInteger()),
    sa.Column("metadata", JSONB(), nullable=False, server_default="{}"),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
)

worker_usage_records = sa.Table(
    "worker_usage_records",
    metadata,
    sa.Column("id", sa.UUID(), primary_key=True),
    sa.Column("run_id", sa.UUID(), sa.ForeignKey("worker_runs.id", ondelete="CASCADE"), nullable=False),
    sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("cost_usd", sa.Numeric(14, 8), nullable=False, server_default="0"),
    sa.Column("duration_ms", sa.Numeric(14, 3), nullable=False, server_default="0"),
    sa.Column("resource_json", JSONB(), nullable=False, server_default="{}"),
    sa.Column("provider_id", sa.Text()),
    sa.Column("exact_model_id", sa.Text()),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
)

hiring_pipeline_stages = sa.Table(
    "hiring_pipeline_stages",
    metadata,
    sa.Column("id", sa.UUID(), primary_key=True),
    sa.Column("worker_id", sa.UUID(), sa.ForeignKey("worker_registry.id", ondelete="CASCADE"), nullable=False),
    sa.Column("stage", sa.Text(), nullable=False),
    sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
    sa.Column("evidence", JSONB(), nullable=False, server_default="{}"),
    sa.Column("completed_by", sa.Text()),
    sa.Column("completed_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
    sa.UniqueConstraint("worker_id", "stage", name="uq_hiring_worker_stage"),
)

approval_records = sa.Table(
    "approval_records",
    metadata,
    sa.Column("id", sa.UUID(), primary_key=True),
    sa.Column("scope_type", sa.Text(), nullable=False),
    sa.Column("scope_id", sa.UUID(), nullable=False),
    sa.Column("decision", sa.Text(), nullable=False),
    sa.Column("decided_by", sa.Text(), nullable=False),
    sa.Column("reason", sa.Text()),
    sa.Column("evidence", JSONB(), nullable=False, server_default="{}"),
    sa.Column("expires_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
)

update_monitoring_jobs = sa.Table(
    "update_monitoring_jobs",
    metadata,
    sa.Column("id", sa.UUID(), primary_key=True),
    sa.Column("worker_id", sa.UUID(), sa.ForeignKey("worker_registry.id", ondelete="CASCADE"), nullable=False),
    sa.Column("steward_id", sa.UUID(), sa.ForeignKey("steward_agents.id", ondelete="SET NULL")),
    sa.Column("cadence", sa.Text(), nullable=False, server_default="daily"),
    sa.Column("last_checked_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("last_candidate_id", sa.UUID()),
    sa.Column("status", sa.Text(), nullable=False, server_default="active"),
    sa.Column("last_error", sa.Text()),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
)

project_repository_records = sa.Table(
    "project_repository_records",
    metadata,
    sa.Column("id", sa.UUID(), primary_key=True),
    sa.Column("project_id", sa.UUID(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
    sa.Column("workspace_path", sa.Text(), nullable=False),
    sa.Column("repository_mode", sa.Text(), nullable=False),
    sa.Column("remote_url", sa.Text()),
    sa.Column("branch", sa.Text()),
    sa.Column("head_commit", sa.Text()),
    sa.Column("dirty", sa.Boolean()),
    sa.Column("last_sync_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("adapter_health", sa.Text(), nullable=False, server_default="unknown"),
    sa.Column("initialized", sa.Boolean(), nullable=False, server_default="false"),
    sa.Column("metadata", JSONB(), nullable=False, server_default="{}"),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
    sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
    sa.UniqueConstraint("project_id", name="uq_project_repository_record"),
)

evidence_policies = sa.Table(
    "evidence_policies",
    metadata,
    sa.Column("id", sa.UUID(), primary_key=True),
    sa.Column("policy_id", sa.Text(), nullable=False),
    sa.Column("version", sa.Text(), nullable=False),
    sa.Column("requirements", JSONB(), nullable=False),
    sa.Column("status", sa.Text(), nullable=False, server_default="approved"),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
    sa.UniqueConstraint("policy_id", "version", name="uq_evidence_policy_version"),
)

project_evidence_packages = sa.Table(
    "project_evidence_packages",
    metadata,
    sa.Column("id", sa.UUID(), primary_key=True),
    sa.Column("project_id", sa.UUID(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
    sa.Column("policy_id", sa.Text(), nullable=False),
    sa.Column("policy_version", sa.Text(), nullable=False),
    sa.Column("status", sa.Text(), nullable=False, server_default="incomplete"),
    sa.Column("checks", JSONB(), nullable=False, server_default="{}"),
    sa.Column("evidence_refs", JSONB(), nullable=False, server_default="{}"),
    sa.Column("completeness_score", sa.Numeric(5, 4), nullable=False, server_default="0"),
    sa.Column("generated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
    sa.UniqueConstraint("project_id", "policy_id", "policy_version", name="uq_project_evidence_policy"),
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
    sa.Column("task_id", sa.UUID(), nullable=True),
    sa.Column("department_id", sa.UUID(), nullable=True),
    sa.Column("active_node_ids", sa.ARRAY(sa.Text()), nullable=False, server_default="{}"),
    sa.Column("status", sa.Text(), nullable=False, server_default="NOT_STARTED"),
    sa.Column("context_json", JSONB(), nullable=False, server_default="{}"),
    sa.Column("started_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("completed_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("retry_count", sa.Integer(), server_default="0"),
    sa.Column("max_retries", sa.Integer(), server_default="3"),
    sa.Column("escalated_to", sa.Text()),
    sa.Column("escalation_reason", sa.Text()),
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
    sa.Column("retry_count", sa.Integer(), server_default="0"),
    sa.Column("max_retries", sa.Integer(), server_default="3"),
    sa.Column("timeout_at", sa.TIMESTAMP(timezone=True)),
    sa.Column(
        "started_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
    sa.Column("completed_at", sa.TIMESTAMP(timezone=True)),
)
