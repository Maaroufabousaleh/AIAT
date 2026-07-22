"""Add the universal worker, steward, model-governance, and evidence records.

Revision ID: 0015_worker_steward_model_governance
Revises: 0014_project_usage_events
Create Date: 2026-07-19

All versioned runtime/configuration records are append-only. Mutable registry
rows may point at an active version, while worker runs retain their own
references so historical executions remain reproducible.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0015_worker_governance"
down_revision = "0014_project_usage_events"
branch_labels = None
depends_on = None


def _ts(name: str, *, nullable: bool = True) -> sa.Column:
    return sa.Column(name, sa.TIMESTAMP(timezone=True), nullable=nullable, server_default=sa.text("now()") if not nullable else None)


def _json(name: str, *, nullable: bool = True, default: str | None = None) -> sa.Column:
    return sa.Column(name, JSONB(), nullable=nullable, server_default=default)


def _text_array(name: str, *, nullable: bool = False) -> sa.Column:
    return sa.Column(name, sa.ARRAY(sa.Text()), nullable=nullable, server_default="{}" if not nullable else None)


def upgrade() -> None:
    # Shell, adapter, provenance, and steward records.
    op.create_table(
        "worker_shell_versions",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("worker_id", sa.UUID(), sa.ForeignKey("worker_registry.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("contract_version", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.Text(), nullable=False),
        _json("identity_json", nullable=False, default="{}"),
        _json("capabilities_json", nullable=False, default="{}"),
        _json("permissions_json", nullable=False, default="{}"),
        sa.Column("model_mode", sa.Text(), nullable=False, server_default="none"),
        _json("provenance_json", nullable=False, default="{}"),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("content_hash", sa.Text(), nullable=False),
        _ts("created_at", nullable=False),
        sa.UniqueConstraint("worker_id", "version", name="uq_worker_shell_version"),
    )
    op.create_table(
        "runtime_adapters",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("worker_id", sa.UUID(), sa.ForeignKey("worker_registry.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("adapter_type", sa.Text(), nullable=False),
        sa.Column("transport_type", sa.Text(), nullable=False),
        sa.Column("runtime_api_version", sa.Text()),
        sa.Column("implementation_ref", sa.Text()),
        sa.Column("content_hash", sa.Text(), nullable=False),
        _json("capabilities_json", nullable=False, default="{}"),
        sa.Column("conformance_status", sa.Text(), nullable=False, server_default="pending"),
        _json("conformance_json"),
        sa.Column("status", sa.Text(), nullable=False, server_default="candidate"),
        _ts("created_at", nullable=False),
        sa.UniqueConstraint("worker_id", "version", name="uq_runtime_adapter_version"),
    )
    op.create_table(
        "external_runtime_provenance",
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
        _ts("created_at", nullable=False),
    )
    op.create_table(
        "steward_agents",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("worker_id", sa.UUID(), sa.ForeignKey("worker_registry.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="PROVISIONING"),
        sa.Column("steward_version", sa.Text(), nullable=False, server_default="1.0.0"),
        sa.Column("provenance_id", sa.UUID(), sa.ForeignKey("external_runtime_provenance.id", ondelete="RESTRICT")),
        sa.Column("active_skill_bundle_id", sa.UUID()),
        sa.Column("active_adapter_id", sa.UUID()),
        sa.Column("monitoring_cadence", sa.Text(), nullable=False, server_default="daily"),
        sa.Column("last_monitor_at", sa.TIMESTAMP(timezone=True)),
        _json("metadata", nullable=False, default="{}"),
        _ts("created_at", nullable=False),
        _ts("updated_at", nullable=False),
        sa.UniqueConstraint("worker_id", name="uq_steward_worker"),
    )
    op.create_table(
        "documentation_sources",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("steward_id", sa.UUID(), sa.ForeignKey("steward_agents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("uri", sa.Text(), nullable=False),
        sa.Column("source_type", sa.Text(), nullable=False, server_default="official"),
        sa.Column("trusted_for_provenance", sa.Boolean(), nullable=False, server_default="false"),
        _text_array("allowed_domains"),
        _ts("created_at", nullable=False),
    )
    op.create_table(
        "documentation_snapshots",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("source_id", sa.UUID(), sa.ForeignKey("documentation_sources.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.Text(), nullable=False),
        sa.Column("content_ref", sa.Text()),
        _json("extracted_interfaces", nullable=False, default="{}"),
        _text_array("security_findings"),
        sa.Column("untrusted", sa.Boolean(), nullable=False, server_default="true"),
        _ts("captured_at", nullable=False),
        sa.UniqueConstraint("source_id", "version", name="uq_documentation_snapshot_version"),
    )
    op.create_table(
        "capability_snapshots",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("worker_id", sa.UUID(), sa.ForeignKey("worker_registry.id", ondelete="CASCADE"), nullable=False),
        sa.Column("steward_id", sa.UUID(), sa.ForeignKey("steward_agents.id", ondelete="SET NULL")),
        sa.Column("version", sa.Text(), nullable=False),
        _json("capabilities_json", nullable=False),
        _text_array("evidence_refs"),
        _ts("created_at", nullable=False),
    )
    op.create_table(
        "compatibility_matrices",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("worker_id", sa.UUID(), sa.ForeignKey("worker_registry.id", ondelete="CASCADE"), nullable=False),
        sa.Column("runtime_version", sa.Text(), nullable=False),
        sa.Column("adapter_version", sa.Text(), nullable=False),
        sa.Column("contract_version", sa.Text(), nullable=False),
        _json("model_profiles_json", nullable=False, default="{}"),
        _json("capabilities_json", nullable=False, default="{}"),
        _text_array("fixtures"),
        sa.Column("passed", sa.Boolean(), nullable=False, server_default="false"),
        _ts("created_at", nullable=False),
    )

    # Candidate bundles and certification evidence.
    op.create_table(
        "certification_runs",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("worker_id", sa.UUID(), sa.ForeignKey("worker_registry.id", ondelete="CASCADE"), nullable=False),
        sa.Column("steward_id", sa.UUID(), sa.ForeignKey("steward_agents.id", ondelete="SET NULL")),
        sa.Column("candidate_id", sa.UUID()),
        sa.Column("status", sa.Text(), nullable=False, server_default="running"),
        _json("conformance_json", nullable=False, default="{}"),
        _json("checks_json", nullable=False, default="{}"),
        _json("evidence_json", nullable=False, default="{}"),
        _text_array("failure_reasons"),
        _ts("started_at", nullable=False),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True)),
    )
    op.create_table(
        "skill_bundles",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("steward_id", sa.UUID(), sa.ForeignKey("steward_agents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("worker_id", sa.UUID(), sa.ForeignKey("worker_registry.id", ondelete="CASCADE"), nullable=False),
        sa.Column("semantic_version", sa.Text(), nullable=False),
        sa.Column("format_version", sa.Text(), nullable=False),
        sa.Column("upstream_compatibility_range", sa.Text(), nullable=False),
        _json("provenance_json", nullable=False),
        _json("bundle_json", nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="DRAFT"),
        _ts("created_at", nullable=False),
        sa.UniqueConstraint("steward_id", "semantic_version", name="uq_skill_bundle_version"),
    )
    op.create_table(
        "skill_bundle_candidates",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("skill_bundle_id", sa.UUID(), sa.ForeignKey("skill_bundles.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("adapter_id", sa.UUID(), sa.ForeignKey("runtime_adapters.id", ondelete="RESTRICT")),
        sa.Column("worker_id", sa.UUID(), sa.ForeignKey("worker_registry.id", ondelete="CASCADE"), nullable=False),
        sa.Column("intake_status", sa.Text(), nullable=False, server_default="DISCOVERED"),
        _json("diff_json", nullable=False, default="{}"),
        _json("evidence_json", nullable=False, default="{}"),
        sa.Column("certification_run_id", sa.UUID(), sa.ForeignKey("certification_runs.id", ondelete="SET NULL")),
        sa.Column("approval_record_id", sa.UUID()),
        _ts("created_at", nullable=False),
    )

    # Governed model profiles and immutable run resolution snapshots.
    op.create_table(
        "model_profiles",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("logical_profile_id", sa.Text(), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        _text_array("approved_provider_ids"),
        _text_array("required_capabilities"),
        _text_array("fallback_profile_ids"),
        sa.Column("status", sa.Text(), nullable=False, server_default="draft"),
        sa.Column("owner", sa.Text(), nullable=False, server_default="aiat"),
        _ts("created_at", nullable=False),
        sa.UniqueConstraint("logical_profile_id", name="uq_model_profile_logical_id"),
    )
    op.create_table(
        "model_profile_versions",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("profile_id", sa.UUID(), sa.ForeignKey("model_profiles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("provider_id", sa.Text(), nullable=False),
        sa.Column("exact_model_id", sa.Text(), nullable=False),
        sa.Column("api_version", sa.Text()),
        _text_array("capabilities"),
        _json("constraints_json", nullable=False, default="{}"),
        _json("provider_settings", nullable=False, default="{}"),
        sa.Column("status", sa.Text(), nullable=False, server_default="draft"),
        sa.Column("effective_from", sa.TIMESTAMP(timezone=True)),
        sa.Column("effective_until", sa.TIMESTAMP(timezone=True)),
        _ts("created_at", nullable=False),
        sa.UniqueConstraint("profile_id", "version", name="uq_model_profile_version"),
    )
    op.create_table(
        "model_resolution_snapshots",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("project_id", sa.UUID(), sa.ForeignKey("projects.id", ondelete="SET NULL")),
        sa.Column("requested_profile_id", sa.Text()),
        sa.Column("resolved_profile_id", sa.Text()),
        sa.Column("resolved_profile_version", sa.Text()),
        sa.Column("provider_id", sa.Text()),
        sa.Column("exact_model_id", sa.Text()),
        _json("effective_constraints", nullable=False, default="{}"),
        _json("effective_configuration", nullable=False, default="{}"),
        _json("capability_checks", nullable=False, default="{}"),
        _json("rejected_candidates", nullable=False, default="[]"),
        _text_array("fallback_chain"),
        sa.Column("cost_estimate_usd", sa.Numeric(14, 8), nullable=False, server_default="0"),
        sa.Column("override_approval_id", sa.UUID()),
        sa.Column("selection_reason", sa.Text()),
        sa.Column("policy_failure_code", sa.Text()),
        _ts("created_at", nullable=False),
    )
    op.create_table(
        "model_override_requests",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("project_id", sa.UUID(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("requested_by", sa.Text(), nullable=False),
        sa.Column("requested_profile_id", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        _json("scope", nullable=False, default="{}"),
        sa.Column("status", sa.Text(), nullable=False, server_default="PENDING"),
        sa.Column("decided_by", sa.Text()),
        sa.Column("decision", sa.Text()),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True)),
        _ts("created_at", nullable=False),
        sa.Column("decided_at", sa.TIMESTAMP(timezone=True)),
    )

    # Rollouts and worker runs.
    op.create_table(
        "rollout_records",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("worker_id", sa.UUID(), sa.ForeignKey("worker_registry.id", ondelete="CASCADE"), nullable=False),
        sa.Column("steward_id", sa.UUID(), sa.ForeignKey("steward_agents.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("candidate_id", sa.UUID(), sa.ForeignKey("skill_bundle_candidates.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="PENDING"),
        _text_array("eligible_task_classes"),
        sa.Column("sample_count", sa.Integer(), nullable=False, server_default="0"),
        _json("sample_targets", nullable=False, default="{}"),
        _json("comparison_metrics", nullable=False, default="{}"),
        _json("rollback_thresholds", nullable=False, default="{}"),
        sa.Column("in_flight_policy", sa.Text(), nullable=False, server_default="finish_pinned_version"),
        sa.Column("promotion_actor", sa.Text()),
        sa.Column("rollback_reason", sa.Text()),
        _ts("started_at", nullable=False),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True)),
        sa.UniqueConstraint("worker_id", "candidate_id", name="uq_rollout_worker_candidate"),
    )
    op.create_table(
        "rollback_records",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("rollout_id", sa.UUID(), sa.ForeignKey("rollout_records.id", ondelete="CASCADE"), nullable=False),
        sa.Column("worker_id", sa.UUID(), sa.ForeignKey("worker_registry.id", ondelete="CASCADE"), nullable=False),
        sa.Column("from_candidate_id", sa.UUID()),
        sa.Column("target_candidate_id", sa.UUID()),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("triggered_by", sa.Text(), nullable=False),
        _json("evidence", nullable=False, default="{}"),
        _ts("created_at", nullable=False),
    )
    op.create_table(
        "worker_runs",
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
        _json("request_json", nullable=False),
        _json("negotiation_json", nullable=False, default="{}"),
        _json("result_json"),
        _json("error_json"),
        _json("replay_metadata", nullable=False, default="{}"),
        _ts("created_at", nullable=False),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True)),
        sa.UniqueConstraint("worker_id", "idempotency_key", name="uq_worker_run_idempotency"),
    )
    op.create_table(
        "worker_events",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("run_id", sa.UUID(), sa.ForeignKey("worker_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        _json("event_json", nullable=False),
        sa.Column("event_sha256", sa.Text(), nullable=False),
        _ts("occurred_at", nullable=False),
        sa.UniqueConstraint("run_id", "sequence", name="uq_worker_event_sequence"),
    )
    op.create_table(
        "worker_checkpoints",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("run_id", sa.UUID(), sa.ForeignKey("worker_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        _json("state_json", nullable=False),
        sa.Column("artifact_id", sa.BigInteger(), sa.ForeignKey("artifacts.id", ondelete="SET NULL")),
        sa.Column("resumable", sa.Boolean(), nullable=False, server_default="true"),
        _ts("created_at", nullable=False),
        sa.UniqueConstraint("run_id", "sequence", name="uq_worker_checkpoint_sequence"),
    )
    op.create_table(
        "worker_artifacts",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("run_id", sa.UUID(), sa.ForeignKey("worker_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("artifact_id", sa.BigInteger(), sa.ForeignKey("artifacts.id", ondelete="RESTRICT")),
        sa.Column("kind", sa.Text(), nullable=False, server_default="other"),
        sa.Column("uri", sa.Text(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger()),
        _json("metadata", nullable=False, default="{}"),
        _ts("created_at", nullable=False),
    )
    op.create_table(
        "worker_usage_records",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("run_id", sa.UUID(), sa.ForeignKey("worker_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Numeric(14, 8), nullable=False, server_default="0"),
        sa.Column("duration_ms", sa.Numeric(14, 3), nullable=False, server_default="0"),
        _json("resource_json", nullable=False, default="{}"),
        sa.Column("provider_id", sa.Text()),
        sa.Column("exact_model_id", sa.Text()),
        _ts("created_at", nullable=False),
    )

    # Hiring, approvals, monitoring, project repository, and evidence policy.
    op.create_table(
        "hiring_pipeline_stages",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("worker_id", sa.UUID(), sa.ForeignKey("worker_registry.id", ondelete="CASCADE"), nullable=False),
        sa.Column("stage", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        _json("evidence", nullable=False, default="{}"),
        sa.Column("completed_by", sa.Text()),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True)),
        _ts("created_at", nullable=False),
        sa.UniqueConstraint("worker_id", "stage", name="uq_hiring_worker_stage"),
    )
    op.create_table(
        "approval_records",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("scope_type", sa.Text(), nullable=False),
        sa.Column("scope_id", sa.UUID(), nullable=False),
        sa.Column("decision", sa.Text(), nullable=False),
        sa.Column("decided_by", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text()),
        _json("evidence", nullable=False, default="{}"),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True)),
        _ts("created_at", nullable=False),
    )
    op.create_table(
        "update_monitoring_jobs",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("worker_id", sa.UUID(), sa.ForeignKey("worker_registry.id", ondelete="CASCADE"), nullable=False),
        sa.Column("steward_id", sa.UUID(), sa.ForeignKey("steward_agents.id", ondelete="SET NULL")),
        sa.Column("cadence", sa.Text(), nullable=False, server_default="daily"),
        sa.Column("last_checked_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("last_candidate_id", sa.UUID()),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("last_error", sa.Text()),
        _ts("created_at", nullable=False),
    )
    op.create_table(
        "project_repository_records",
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
        _json("metadata", nullable=False, default="{}"),
        _ts("created_at", nullable=False),
        _ts("updated_at", nullable=False),
        sa.UniqueConstraint("project_id", name="uq_project_repository_record"),
    )
    op.create_table(
        "evidence_policies",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("policy_id", sa.Text(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        _json("requirements", nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="approved"),
        _ts("created_at", nullable=False),
        sa.UniqueConstraint("policy_id", "version", name="uq_evidence_policy_version"),
    )
    op.create_table(
        "project_evidence_packages",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("project_id", sa.UUID(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("policy_id", sa.Text(), nullable=False),
        sa.Column("policy_version", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="incomplete"),
        _json("checks", nullable=False, default="{}"),
        _json("evidence_refs", nullable=False, default="{}"),
        sa.Column("completeness_score", sa.Numeric(5, 4), nullable=False, server_default="0"),
        _ts("generated_at", nullable=False),
        sa.UniqueConstraint("project_id", "policy_id", "policy_version", name="uq_project_evidence_policy"),
    )

    # The registry remains the mutable source of the currently selected
    # governed versions.  These pointers are deliberately added after the
    # immutable tables exist so their foreign keys can be created safely.
    op.add_column("worker_registry", sa.Column("active_shell_version_id", sa.UUID(), nullable=True))
    op.add_column("worker_registry", sa.Column("active_adapter_id", sa.UUID(), nullable=True))
    op.add_column("worker_registry", sa.Column("active_skill_bundle_id", sa.UUID(), nullable=True))
    op.add_column("worker_registry", sa.Column("model_profile_id", sa.Text(), nullable=True))
    op.add_column("worker_registry", sa.Column("model_mode", sa.Text(), nullable=False, server_default="none"))
    op.create_foreign_key("fk_worker_registry_active_shell", "worker_registry", "worker_shell_versions", ["active_shell_version_id"], ["id"], ondelete="RESTRICT")
    op.create_foreign_key("fk_worker_registry_active_adapter", "worker_registry", "runtime_adapters", ["active_adapter_id"], ["id"], ondelete="RESTRICT")
    op.create_foreign_key("fk_worker_registry_active_skill", "worker_registry", "skill_bundles", ["active_skill_bundle_id"], ["id"], ondelete="RESTRICT")

    # Immutable lookup indexes and the most important project/run scopes.
    op.create_index("idx_worker_runs_project_created", "worker_runs", ["project_id", "created_at"])
    op.create_index("idx_worker_events_run_time", "worker_events", ["run_id", "occurred_at"])
    op.create_index("idx_documentation_snapshots_source_time", "documentation_snapshots", ["source_id", "captured_at"])
    op.create_index("idx_project_evidence_status", "project_evidence_packages", ["project_id", "status"])


def downgrade() -> None:
    for index_name, table_name in (
        ("idx_project_evidence_status", "project_evidence_packages"),
        ("idx_documentation_snapshots_source_time", "documentation_snapshots"),
        ("idx_worker_events_run_time", "worker_events"),
        ("idx_worker_runs_project_created", "worker_runs"),
    ):
        op.drop_index(index_name, table_name=table_name)
    op.drop_constraint("fk_worker_registry_active_skill", "worker_registry", type_="foreignkey")
    op.drop_constraint("fk_worker_registry_active_adapter", "worker_registry", type_="foreignkey")
    op.drop_constraint("fk_worker_registry_active_shell", "worker_registry", type_="foreignkey")
    op.drop_column("worker_registry", "model_mode")
    op.drop_column("worker_registry", "model_profile_id")
    op.drop_column("worker_registry", "active_skill_bundle_id")
    op.drop_column("worker_registry", "active_adapter_id")
    op.drop_column("worker_registry", "active_shell_version_id")
    for table_name in (
        "project_evidence_packages",
        "evidence_policies",
        "project_repository_records",
        "update_monitoring_jobs",
        "approval_records",
        "hiring_pipeline_stages",
        "worker_usage_records",
        "worker_artifacts",
        "worker_checkpoints",
        "worker_events",
        "worker_runs",
        "rollback_records",
        "rollout_records",
        "model_override_requests",
        "model_resolution_snapshots",
        "model_profile_versions",
        "model_profiles",
        "skill_bundle_candidates",
        "skill_bundles",
        "certification_runs",
        "compatibility_matrices",
        "capability_snapshots",
        "documentation_snapshots",
        "documentation_sources",
        "steward_agents",
        "external_runtime_provenance",
        "runtime_adapters",
        "worker_shell_versions",
    ):
        op.drop_table(table_name)
