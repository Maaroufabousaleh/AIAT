"""Tests for Phase 7 — Storage Layer (Postgres + MinIO).

Test classes
------------
TestModelsMetadata       — Verify canonical 20 tables in SQLAlchemy metadata, columns, constraints.
TestAgentStorageCRUD     — AgentStorage mocked-engine CRUD tests for all table families.
TestCheckpointStore      — CheckpointStore save/load/delete with mocked engine.
TestBlobClient           — BlobClient upload/download/delete/list with mocked S3.
TestBlobRef              — BlobRef dataclass serialisation round-trip.
TestMemoryInit           — Public API re-exports from mas_core.memory.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

# ── Blob ──────────────────────────────────────────────────────────────────────
from mas_core.memory.blob import BlobClient, BlobRef

# ── Checkpoints ───────────────────────────────────────────────────────────────
from mas_core.memory.checkpoints import CheckpointStore

# ── Models ────────────────────────────────────────────────────────────────────
from mas_core.memory.models import (
    agent_checkpoints,
    agent_profiles,
    approval_gates,
    capabilities,
    compatibility_matrices,
    dead_letters,
    documents,
    infra_events,
    issues,
    kpi_snapshots,
    metadata,
    native_trace_spans,
    project_state_history,
    projects,
    review_comments,
    review_sessions,
    role_capability_map,
    sprints,
    system_config,
    worker_registry,
)

# ── Storage ───────────────────────────────────────────────────────────────────
from mas_core.memory.storage import AgentStorage
from mas_core.workflow import (
    ImprovementArtifact,
    ImprovementArtifactBundle,
    ImprovementArtifactKind,
    ImprovementOpportunity,
    ImprovementOutcomeKind,
    ImprovementRisk,
    ImprovementStatus,
)

# ===========================================================================
# Helpers
# ===========================================================================


def _make_mock_engine() -> MagicMock:
    """Build a mock AsyncEngine that exposes .begin() and .connect() as async CMs."""
    engine = MagicMock()
    conn = AsyncMock()

    # .begin() → async context manager → yields conn
    begin_cm = AsyncMock()
    begin_cm.__aenter__ = AsyncMock(return_value=conn)
    begin_cm.__aexit__ = AsyncMock(return_value=False)
    engine.begin.return_value = begin_cm

    # .connect() → async context manager → yields conn
    connect_cm = AsyncMock()
    connect_cm.__aenter__ = AsyncMock(return_value=conn)
    connect_cm.__aexit__ = AsyncMock(return_value=False)
    engine.connect.return_value = connect_cm

    # Store conn for assertions
    engine._mock_conn = conn
    return engine


def _mock_row(data: dict) -> MagicMock:
    """Return an object that acts like a SQLAlchemy Row for .mappings().first()."""
    row = MagicMock()
    row.__getitem__ = lambda self, key: data[key]
    row.keys = lambda: data.keys()
    # dict(row) support
    row.__iter__ = lambda self: iter(data)
    return row


def _mock_mappings(rows: list[dict]) -> MagicMock:
    """Mock CursorResult().mappings() supporting .first() and .all()."""
    mappings_mock = MagicMock()
    if rows:
        # Each mapping is a MappingResult that supports dict()
        mapping_objects = []
        for r in rows:
            m = MagicMock()
            m.__iter__ = lambda self, _r=r: iter(_r)
            m.__getitem__ = lambda self, key, _r=r: _r[key]
            m.keys = lambda _r=r: _r.keys()
            mapping_objects.append(m)
        mappings_mock.first.return_value = mapping_objects[0]
        mappings_mock.all.return_value = mapping_objects
    else:
        mappings_mock.first.return_value = None
        mappings_mock.all.return_value = []
    return mappings_mock


# ===========================================================================
# TestModelsMetadata
# ===========================================================================


class TestModelsMetadata:
    """Verify that the current canonical table set is fully represented."""

    EXPECTED_TABLES = [
        "projects",
        "project_state_history",
        "documents",
        "review_sessions",
        "review_comments",
        "approval_gates",
        "sprints",
        "issues",
        "kpi_snapshots",
        "agent_profiles",
        "dead_letters",
        "system_config",
        "project_context_items",
        "project_context_chunks",
        "project_context_tags",
        "project_context_relations",
        "agent_checkpoints",
        "memory",
        "task_log",
        "api_request_observations",
        "native_trace_spans",
        "artifacts",
        "infra_events",
        "capabilities",
        "worker_registry",
        "worker_hosts",
        "worker_host_reservations",
        "evaluation_reports",
        "project_usage_events",
        "worker_shell_versions",
        "runtime_adapters",
        "external_runtime_provenance",
        "steward_agents",
        "steward_transitions",
        "documentation_sources",
        "documentation_snapshots",
        "capability_snapshots",
        "compatibility_matrices",
        "certification_runs",
        "skill_bundles",
        "skill_bundle_candidates",
        "model_profiles",
        "model_profile_versions",
        "model_resolution_snapshots",
        "model_override_requests",
        "rollout_records",
        "rollout_transitions",
        "rollback_records",
        "worker_runs",
        "worker_run_transitions",
        "worker_events",
        "worker_checkpoints",
        "worker_artifacts",
        "worker_usage_records",
        "worker_run_host_bindings",
        "hiring_pipeline_stages",
        "approval_records",
        "update_monitoring_jobs",
        "project_repository_records",
        "evidence_policies",
        "project_evidence_packages",
        "role_capability_map",
        "flows",
        "flow_instances",
        "flow_node_executions",
        "pm_connections",
        "pm_project_bindings",
        "pm_object_mappings",
        "pm_external_actor_mappings",
        "pm_external_actor_mapping_audits",
        "pm_inbox_events",
        "pm_outbox_events",
        "pm_outbox_dispositions",
        "pm_delivery_attempts",
        "pm_conflicts",
        "pm_reconciliation_runs",
        "pm_cutovers",
        "pm_lifecycle_plans",
        "pm_lifecycle_audits",
        "pm_inbound_canary_plans",
        "work_item_comments",
        "work_item_links",
        "integration_evidence_records",
        "companies",
        "company_manifest_versions",
        "company_departments",
        "company_worker_assignments",
        "company_budgets",
        "budget_reservations",
    ]

    def test_all_current_tables_present(self):
        """Metadata should contain exactly the declared canonical table set."""
        assert set(metadata.tables) == set(self.EXPECTED_TABLES)

    def test_capability_registry_tables_defined(self):
        assert capabilities.name == "capabilities"
        assert worker_registry.name == "worker_registry"
        assert role_capability_map.name == "role_capability_map"
        assert infra_events.name == "infra_events"

    def test_projects_columns(self):
        """projects table must have the expected column set."""
        cols = {c.name for c in projects.columns}
        expected = {
            "id",
            "company_id",
            "name",
            "description",
            "state",
            "failure_reason",
            "failed_from_state",
            "created_by",
            "human_requester",
            "config",
            "revision",
            "created_at",
            "updated_at",
        }
        assert expected == cols

    def test_project_state_history_columns(self):
        cols = {c.name for c in project_state_history.columns}
        assert "from_state" in cols
        assert "to_state" in cols
        assert "event" in cols
        assert "project_id" in cols

    def test_integration_evidence_columns(self):
        from mas_core.memory.models import integration_evidence_records

        cols = {c.name for c in integration_evidence_records.columns}
        assert {"connection_id", "evidence_type", "payload", "idempotency_key", "trace_id", "span_id"} <= cols

    def test_native_trace_span_columns_are_payload_free(self):
        cols = {c.name for c in native_trace_spans.columns}
        assert {
            "trace_id",
            "span_id",
            "parent_span_id",
            "source_kind",
            "operation",
            "service",
            "status",
            "started_at",
            "ended_at",
            "duration_ms",
            "sampled",
            "retention_until",
            "attributes_json",
        } <= cols
        assert "payload" not in cols

    def test_worker_evidence_columns_include_trace_context(self):
        from mas_core.memory.models import worker_artifacts, worker_usage_records

        assert {"trace_id", "span_id"} <= {c.name for c in worker_artifacts.columns}
        assert {"trace_id", "span_id"} <= {c.name for c in worker_usage_records.columns}

    def test_pm_forensics_columns(self):
        from mas_core.memory.models import (
            pm_inbound_canary_plans,
            pm_inbox_events,
            work_item_comments,
        )

        assert {"raw_body", "headers", "normalized_type", "result"} <= {c.name for c in pm_inbox_events.columns}
        assert {"approval_id", "body_blob_ref"} <= {c.name for c in work_item_comments.columns}
        assert {"expired_by", "expired_at"} <= {
            c.name for c in pm_inbound_canary_plans.columns
        }

    def test_documents_columns(self):
        cols = {c.name for c in documents.columns}
        expected = {
            "id",
            "project_id",
            "lineage_id",
            "doc_type",
            "version",
            "status",
            "blob_bucket",
            "blob_key",
            "blob_sha256",
            "created_by",
            "created_at",
            "updated_at",
        }
        assert expected == cols

    def test_review_sessions_has_reviewer_ids(self):
        cols = {c.name for c in review_sessions.columns}
        assert "reviewer_ids" in cols
        assert "session_type" in cols
        assert "review_timeout_seconds" in cols

    def test_review_comments_has_verdict_and_veto(self):
        cols = {c.name for c in review_comments.columns}
        assert "verdict" in cols
        assert "veto" in cols
        assert "severity" in cols

    def test_approval_gates_columns(self):
        cols = {c.name for c in approval_gates.columns}
        assert "gate_type" in cols
        assert "status" in cols
        assert "decided_by" in cols

    def test_sprints_columns(self):
        cols = {c.name for c in sprints.columns}
        assert "sprint_number" in cols
        assert "planned_story_points" in cols
        assert "estimated_hours" in cols
        assert "infra_requested_at" in cols

    def test_issues_columns(self):
        cols = {c.name for c in issues.columns}
        assert "title" in cols
        assert "issue_type" in cols
        assert "priority" in cols
        assert "dependencies" in cols
        assert "story_points" in cols

    def test_kpi_snapshots_columns(self):
        cols = {c.name for c in kpi_snapshots.columns}
        expected_metrics = {
            "estimation_accuracy",
            "task_completion_rate",
            "review_pass_rate",
            "velocity",
            "defect_rate",
            "rework_rate",
            "budget_adherence",
            "resource_utilization",
            "infra_lead_time_seconds",
        }
        assert expected_metrics.issubset(cols)

    def test_agent_profiles_pk_is_agent_id(self):
        pk_cols = [c.name for c in agent_profiles.primary_key.columns]
        assert pk_cols == ["agent_id"]

    def test_dead_letters_columns(self):
        cols = {c.name for c in dead_letters.columns}
        assert "envelope_json" in cols
        assert "failure_reason" in cols
        assert "retry_count" in cols

    def test_system_config_pk_is_key(self):
        pk_cols = [c.name for c in system_config.primary_key.columns]
        assert pk_cols == ["key"]

    def test_agent_checkpoints_unique_constraint(self):
        """agent_checkpoints should have a unique constraint on (agent_id, task_message_id)."""
        constraints = [
            c
            for c in agent_checkpoints.constraints
            if hasattr(c, "name") and c.name == "uq_checkpoint_agent_task"
        ]
        assert len(constraints) == 1

    def test_projects_foreign_key_cascade(self):
        """project_state_history.project_id should cascade on delete."""
        fk = list(project_state_history.c.project_id.foreign_keys)[0]
        assert fk.ondelete == "CASCADE"


# ===========================================================================
# TestAgentStorageCRUD
# ===========================================================================


class TestAgentStorageCRUD:
    """Test AgentStorage methods with a mocked engine."""

    def _make_storage(self) -> tuple[AgentStorage, MagicMock]:
        """Create an AgentStorage with a patched engine."""
        storage = AgentStorage.__new__(AgentStorage)
        engine = _make_mock_engine()
        storage._engine = engine
        storage._dsn = "postgresql+asyncpg://test:test@localhost/test"
        return storage, engine

    @pytest.mark.asyncio
    async def test_compatibility_matrix_writer_persists_bounded_evidence(self):
        storage, engine = self._make_storage()
        conn = engine._mock_conn
        conn.execute = AsyncMock()
        worker_id = uuid4()

        result = await storage.create_compatibility_matrix(
            worker_id=worker_id,
            runtime_version="1.17.13",
            adapter_version="1.0.0",
            contract_version="aiat.adapter.v1",
            model_profiles={"worker": "opencode-phase0b-coding"},
            capabilities={"task_types": ["code"]},
            fixtures=["worker_contract", "canary"],
            passed=False,
        )

        assert result["worker_id"] == worker_id
        assert result["fixtures"] == ["worker_contract", "canary"]
        assert result["passed"] is False
        assert conn.execute.await_count == 1
        assert compatibility_matrices.name == "compatibility_matrices"

    @pytest.mark.asyncio
    async def test_native_trace_span_writer_is_payload_free_and_queryable(self):
        storage, engine = self._make_storage()
        conn = engine._mock_conn
        conn.execute = AsyncMock()
        span = await storage.create_native_trace_span(
            trace_id="trace-storage-001",
            span_id="span-storage-001",
            source_kind="tool",
            operation="clock.now",
            service="tool_service",
            status="success",
            duration_ms=3,
            attributes={
                "tool": "clock.now",
                "request_body": "must-not-persist",
            },
        )
        assert span["trace_id"] == "trace-storage-001"
        assert span["span_id"] == "span-storage-001"
        assert span["attributes_json"] == {"tool": "clock.now"}
        assert conn.execute.await_count == 1

        result_mock = MagicMock()
        result_mock.mappings.return_value = _mock_mappings(
            [{"id": span["id"], "trace_id": "trace-storage-001", "span_id": "span-storage-001"}]
        )
        conn.execute = AsyncMock(return_value=result_mock)
        rows = await storage.list_native_trace_spans_by_trace("trace-storage-001", limit=4)
        assert rows[0]["span_id"] == "span-storage-001"

    # ── Projects ──

    @pytest.mark.asyncio
    async def test_create_project(self):
        storage, engine = self._make_storage()
        conn = engine._mock_conn
        conn.execute = AsyncMock()

        result = await storage.create_project(
            name="Test Project",
            description="A test",
            state="INIT",
            created_by="ceo_agent",
        )
        assert result["name"] == "Test Project"
        assert result["state"] == "INIT"
        assert result["created_by"] == "ceo_agent"
        assert isinstance(result["id"], UUID)
        assert conn.execute.await_count == 2

    @pytest.mark.asyncio
    async def test_create_project_with_initial_context_is_atomic(self):
        storage, engine = self._make_storage()
        conn = engine._mock_conn
        conn.execute = AsyncMock()

        result = await storage.create_project(
            name="Context Project",
            description="A project with a starter brief",
            created_by="human",
            initial_context=[
                {
                    "item_type": "TEXT",
                    "name": "Project goal",
                    "content_text": "Build the workspace",
                    "tags": ["goal"],
                }
            ],
        )

        assert result["name"] == "Context Project"
        assert conn.execute.await_count == 3

    @pytest.mark.asyncio
    async def test_create_project_with_explicit_id(self):
        storage, engine = self._make_storage()
        conn = engine._mock_conn
        conn.execute = AsyncMock()
        pid = uuid4()

        result = await storage.create_project(
            name="Explicit",
            created_by="admin",
            project_id=pid,
        )
        assert result["id"] == pid

    @pytest.mark.asyncio
    async def test_create_self_improvement_project_uses_canonical_project_writer(self):
        storage, engine = self._make_storage()
        conn = engine._mock_conn
        conn.execute = AsyncMock()
        pid = uuid4()
        opportunity = ImprovementOpportunity(
            title="Storage-backed improvement",
            description="Persist a governed improvement request.",
            owner="cto",
            risk=ImprovementRisk.MEDIUM,
            budget_usd="3.50",
            evidence_policy="software_delivery",
            source="operator_goal",
            created_by="operator",
            created_by_kind="human",
        )

        result = await storage.create_self_improvement_project(opportunity, project_id=pid)

        assert result["id"] == pid
        assert result["name"] == "Improvement: Storage-backed improvement"
        assert result["config"]["self_improvement"]["risk"] == "medium"
        assert result["config"]["self_improvement"]["budget_usd"] == "3.50"
        assert result["config"]["self_improvement"]["evidence_policy"] == "software_delivery"
        lifecycle = result["config"]["self_improvement"]["lifecycle"]
        assert lifecycle["status"] == "project_bound"
        assert lifecycle["project_id"] == str(pid)
        assert lifecycle["revision"] == 1
        assert conn.execute.await_count == 2

    @pytest.mark.asyncio
    async def test_self_improvement_lifecycle_reads_from_project_config(self):
        storage, engine = self._make_storage()
        conn = engine._mock_conn
        pid = uuid4()
        opportunity = ImprovementOpportunity(
            title="Read lifecycle",
            description="Read durable lifecycle state.",
            owner="operator",
            risk=ImprovementRisk.LOW,
            budget_usd="1.00",
            evidence_policy="software_delivery",
            source="test",
            created_by="operator",
            created_by_kind="human",
        )
        from mas_core.workflow import SelfImprovementLifecycle

        lifecycle = SelfImprovementLifecycle.create(opportunity)
        lifecycle.bind_project(pid, actor="operator", actor_kind="human")
        row = {
            "id": pid,
            "config": {
                "self_improvement": {"lifecycle": lifecycle.as_dict()},
            },
        }
        result_mock = MagicMock()
        result_mock.mappings.return_value = _mock_mappings([row])
        conn.execute = AsyncMock(return_value=result_mock)

        snapshot = await storage.get_self_improvement_lifecycle(pid)

        assert snapshot is not None
        assert snapshot["status"] == "project_bound"
        assert snapshot["opportunity"]["description"] == "Read durable lifecycle state."

    @pytest.mark.asyncio
    async def test_self_improvement_lifecycle_update_uses_revision_and_history(self):
        storage, engine = self._make_storage()
        conn = engine._mock_conn
        pid = uuid4()
        opportunity = ImprovementOpportunity(
            title="Update lifecycle",
            description="Persist a linked worker run.",
            owner="operator",
            risk=ImprovementRisk.LOW,
            budget_usd="1.00",
            evidence_policy="software_delivery",
            source="test",
            created_by="operator",
            created_by_kind="human",
        )
        from mas_core.workflow import SelfImprovementLifecycle

        lifecycle = SelfImprovementLifecycle.create(opportunity)
        lifecycle.bind_project(pid, actor="operator", actor_kind="human")
        current_config = {"self_improvement": {"lifecycle": lifecycle.as_dict()}}
        next_lifecycle = SelfImprovementLifecycle.from_dict(lifecycle.as_dict())
        next_lifecycle.link_reference("worker_run", "run-1")
        next_lifecycle.status = ImprovementStatus.REJECTED
        next_lifecycle.record_outcome(
            outcome=ImprovementOutcomeKind.FAILURE,
            cost_usd="2.25",
            incident_count=1,
            kpi_learning={"recovery_minutes": 7.0},
            evidence_refs=("evidence/outcome",),
            actor="operator",
            actor_kind="human",
        )
        next_lifecycle.record_artifact_bundle(
            ImprovementArtifactBundle(
                bundle_id=uuid4(),
                candidate_version="v2",
                generated_by="operator",
                generated_by_kind="human",
                artifacts=tuple(
                    ImprovementArtifact(
                        artifact_id=uuid4(),
                        kind=kind,
                        uri=f"artifact://storage-test/v2/{kind.value}",
                        sha256=(format(index + 1, "x") * 64)[:64],
                        size_bytes=index + 1,
                        candidate_version="v2",
                        source_revision="storage-test-v2",
                    )
                    for index, kind in enumerate(ImprovementArtifactKind)
                ),
            ),
            actor="operator",
            actor_kind="human",
        )
        next_config = {"self_improvement": {"lifecycle": next_lifecycle.as_dict()}}
        current_row = {"id": pid, "config": current_config, "revision": 1}
        updated_row = {"id": pid, "config": next_config, "revision": 2}
        results = [
            MagicMock(mappings=MagicMock(return_value=_mock_mappings([current_row]))),
            MagicMock(rowcount=1),
            MagicMock(),
            MagicMock(mappings=MagicMock(return_value=_mock_mappings([updated_row]))),
            MagicMock(mappings=MagicMock(return_value=_mock_mappings([]))),
            MagicMock(mappings=MagicMock(return_value=_mock_mappings([updated_row]))),
        ]
        conn.execute = AsyncMock(side_effect=results)

        result = await storage.update_self_improvement_lifecycle(
            pid,
            next_lifecycle,
            actor="operator",
        )

        assert result is not None
        assert next_lifecycle.revision == 2
        assert next_lifecycle.as_dict()["outcomes"][0]["cost_usd"] == "2.25"
        assert next_lifecycle.as_dict()["artifact_bundle"]["schema_version"] == (
            "aiat.self-improvement-artifacts.v1"
        )
        assert conn.execute.await_count == 6

    @pytest.mark.asyncio
    async def test_get_project_returns_none_when_missing(self):
        storage, engine = self._make_storage()
        conn = engine._mock_conn
        result_mock = MagicMock()
        result_mock.mappings.return_value = _mock_mappings([])
        conn.execute = AsyncMock(return_value=result_mock)

        project = await storage.get_project(uuid4())
        assert project is None

    @pytest.mark.asyncio
    async def test_get_project_returns_dict(self):
        storage, engine = self._make_storage()
        conn = engine._mock_conn
        pid = uuid4()
        row_data = {"id": pid, "name": "P1", "state": "INIT", "created_by": "admin"}
        result_mock = MagicMock()
        result_mock.mappings.return_value = _mock_mappings([row_data])
        conn.execute = AsyncMock(return_value=result_mock)

        project = await storage.get_project(pid)
        assert project is not None
        assert project["name"] == "P1"

    @pytest.mark.asyncio
    async def test_list_projects_empty(self):
        storage, engine = self._make_storage()
        conn = engine._mock_conn
        result_mock = MagicMock()
        result_mock.mappings.return_value = _mock_mappings([])
        conn.execute = AsyncMock(return_value=result_mock)

        rows = await storage.list_projects()
        assert rows == []

    @pytest.mark.asyncio
    async def test_list_projects_with_state_filter(self):
        storage, engine = self._make_storage()
        conn = engine._mock_conn
        result_mock = MagicMock()
        result_mock.mappings.return_value = _mock_mappings(
            [
                {"id": uuid4(), "name": "A", "state": "PDR_CREATION"},
            ]
        )
        conn.execute = AsyncMock(return_value=result_mock)

        rows = await storage.list_projects(state="PDR_CREATION")
        assert len(rows) == 1

    # ── Documents ──

    @pytest.mark.asyncio
    async def test_create_document(self):
        storage, engine = self._make_storage()
        conn = engine._mock_conn
        conn.execute = AsyncMock()
        pid = uuid4()

        doc = await storage.create_document(
            project_id=pid,
            doc_type="PDR",
            created_by="cto_agent",
        )
        assert doc["doc_type"] == "PDR"
        assert doc["version"] == 1
        assert doc["status"] == "DRAFT"

    @pytest.mark.asyncio
    async def test_document_revision_uses_source_lineage(self):
        """A revision cannot supersede another document of the same type."""
        storage, engine = self._make_storage()
        conn = engine._mock_conn
        source_id = uuid4()
        lineage_id = uuid4()
        latest_id = uuid4()
        source_result = MagicMock()
        source_result.mappings.return_value.first.return_value = {
            "id": source_id,
            "project_id": uuid4(),
            "lineage_id": lineage_id,
            "doc_type": "PDR",
            "version": 1,
        }
        latest_result = MagicMock()
        latest_result.mappings.return_value.first.return_value = {
            "id": latest_id,
            "project_id": source_result.mappings.return_value.first.return_value["project_id"],
            "lineage_id": lineage_id,
            "doc_type": "PDR",
            "version": 2,
        }
        conn.execute = AsyncMock(side_effect=[source_result, latest_result, None, None])

        revision = await storage.create_document_revision(
            source_id,
            created_by="cto_agent",
        )

        assert revision["lineage_id"] == lineage_id
        latest_query = str(conn.execute.await_args_list[1].args[0])
        assert "lineage_id" in latest_query

    @pytest.mark.asyncio
    async def test_get_document_not_found(self):
        storage, engine = self._make_storage()
        conn = engine._mock_conn
        result_mock = MagicMock()
        result_mock.mappings.return_value = _mock_mappings([])
        conn.execute = AsyncMock(return_value=result_mock)

        doc = await storage.get_document(uuid4())
        assert doc is None

    @pytest.mark.asyncio
    async def test_update_document_status(self):
        storage, engine = self._make_storage()
        conn = engine._mock_conn
        conn.execute = AsyncMock()

        await storage.update_document_status(uuid4(), status="APPROVED")
        conn.execute.assert_awaited_once()

    # ── Review Sessions & Comments ──

    @pytest.mark.asyncio
    async def test_create_review_session(self):
        storage, engine = self._make_storage()
        conn = engine._mock_conn
        conn.execute = AsyncMock()

        session = await storage.create_review_session(
            project_id=uuid4(),
            session_type="PDR_REVIEW",
            reviewer_ids=["cto_agent", "coo_agent"],
        )
        assert session["session_type"] == "PDR_REVIEW"
        assert session["status"] == "IN_PROGRESS"
        assert len(session["reviewer_ids"]) == 2

    @pytest.mark.asyncio
    async def test_add_review_comment(self):
        storage, engine = self._make_storage()
        conn = engine._mock_conn
        conn.execute = AsyncMock()

        comment = await storage.add_review_comment(
            session_id=uuid4(),
            project_id=uuid4(),
            reviewer_id="cto_agent",
            reviewer_role="CTO",
            verdict="APPROVE",
        )
        assert comment["verdict"] == "APPROVE"
        assert comment["veto"] is False

    @pytest.mark.asyncio
    async def test_add_review_comment_with_veto(self):
        storage, engine = self._make_storage()
        conn = engine._mock_conn
        conn.execute = AsyncMock()

        comment = await storage.add_review_comment(
            session_id=uuid4(),
            project_id=uuid4(),
            reviewer_id="ceo_agent",
            reviewer_role="CEO",
            verdict="REJECT",
            veto=True,
            severity="CRITICAL",
            comments=[{"line": 42, "text": "Budget too high"}],
        )
        assert comment["veto"] is True
        assert comment["severity"] == "CRITICAL"

    # ── Approval Gates ──

    @pytest.mark.asyncio
    async def test_create_approval_gate(self):
        storage, engine = self._make_storage()
        conn = engine._mock_conn
        conn.execute = AsyncMock()

        gate = await storage.create_approval_gate(
            project_id=uuid4(),
            gate_type="HUMAN_APPROVAL",
        )
        assert gate["gate_type"] == "HUMAN_APPROVAL"
        assert gate["status"] == "PENDING"

    @pytest.mark.asyncio
    async def test_decide_approval_gate(self):
        storage, engine = self._make_storage()
        conn = engine._mock_conn
        conn.execute = AsyncMock(return_value=MagicMock(rowcount=1))

        updated = await storage.decide_approval_gate(
            uuid4(),
            status="APPROVED",
            decided_by="human_operator",
            justification="Looks good",
        )
        assert updated is True
        conn.execute.assert_awaited_once()
        assert "approval_gates.status" in str(conn.execute.await_args.args[0])

    @pytest.mark.asyncio
    async def test_decide_approval_gate_returns_false_when_not_pending(self):
        storage, engine = self._make_storage()
        conn = engine._mock_conn
        conn.execute = AsyncMock(return_value=MagicMock(rowcount=0))

        updated = await storage.decide_approval_gate(
            uuid4(),
            status="APPROVED",
            decided_by="late_operator",
        )

        assert updated is False

    # ── Sprints ──

    @pytest.mark.asyncio
    async def test_create_sprint(self):
        storage, engine = self._make_storage()
        conn = engine._mock_conn
        conn.execute = AsyncMock()

        sprint = await storage.create_sprint(
            project_id=uuid4(),
            sprint_number=1,
            goal="MVP backend",
            planned_story_points=21,
        )
        assert sprint["sprint_number"] == 1
        assert sprint["status"] == "PLANNED"
        assert sprint["goal"] == "MVP backend"

    @pytest.mark.asyncio
    async def test_list_sprints_empty(self):
        storage, engine = self._make_storage()
        conn = engine._mock_conn
        result_mock = MagicMock()
        result_mock.mappings.return_value = _mock_mappings([])
        conn.execute = AsyncMock(return_value=result_mock)

        sprints_list = await storage.list_sprints(uuid4())
        assert sprints_list == []

    # ── Issues ──

    @pytest.mark.asyncio
    async def test_create_issue(self):
        storage, engine = self._make_storage()
        conn = engine._mock_conn
        conn.execute = AsyncMock()

        issue = await storage.create_issue(
            project_id=uuid4(),
            title="Implement login API",
            issue_type="STORY",
            priority="HIGH",
            story_points=5,
        )
        assert issue["title"] == "Implement login API"
        assert issue["issue_type"] == "STORY"
        assert issue["status"] == "backlog"
        assert issue["priority"] == "HIGH"

    @pytest.mark.asyncio
    async def test_create_issue_with_dependencies(self):
        storage, engine = self._make_storage()
        conn = engine._mock_conn
        conn.execute = AsyncMock()
        dep1, dep2 = uuid4(), uuid4()

        issue = await storage.create_issue(
            project_id=uuid4(),
            title="Deploy service",
            issue_type="TASK",
            dependencies=[dep1, dep2],
        )
        assert issue["dependencies"] == [dep1, dep2]

    @pytest.mark.asyncio
    async def test_update_issue(self):
        storage, engine = self._make_storage()
        conn = engine._mock_conn
        conn.execute = AsyncMock()

        await storage.update_issue(uuid4(), status="IN_PROGRESS", assigned_agent="worker_1")
        assert conn.execute.await_count == 2

    # ── KPI Snapshots ──

    @pytest.mark.asyncio
    async def test_save_kpi_snapshot(self):
        storage, engine = self._make_storage()
        conn = engine._mock_conn
        conn.execute = AsyncMock()

        snap = await storage.save_kpi_snapshot(
            project_id=uuid4(),
            scope="SPRINT",
            velocity=Decimal("13.5"),
            task_completion_rate=Decimal("0.85"),
        )
        assert snap["scope"] == "SPRINT"
        assert snap["velocity"] == Decimal("13.5")

    # ── Dead Letters ──

    @pytest.mark.asyncio
    async def test_insert_dead_letter(self):
        storage, engine = self._make_storage()
        conn = engine._mock_conn
        conn.execute = AsyncMock()

        await storage.insert_dead_letter(
            message_id="msg-001",
            recipient_team="dept_production",
            sender_id="ceo_agent",
            msg_type="TASK",
            retry_count=5,
            failure_reason="timeout",
            envelope_json={"msg_type": "TASK"},
        )
        conn.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_settle_budget_reservation_caps_actual_usage_under_lock(self):
        storage, engine = self._make_storage()
        conn = engine._mock_conn
        reservation_id = uuid4()
        company_id = uuid4()
        reservation = {
            "id": reservation_id,
            "company_id": company_id,
            "budget_key": "max_cost_usd",
            "amount": Decimal("2"),
            "state": "RESERVED",
            "metadata": {"source": "worker_dispatch"},
        }
        refreshed = {
            **reservation,
            "amount": Decimal("1"),
            "state": "COMMITTED",
            "metadata": {
                "source": "worker_dispatch",
                "actual_cost_usd": "5",
                "budget_overage_usd": "4",
                "budget_settlement": "CAP_EXCEEDED",
            },
        }

        def result(*, row=None, scalar=None):
            value = MagicMock()
            value.mappings.return_value.first.return_value = row
            value.scalar_one.return_value = scalar
            return value

        conn.execute = AsyncMock(
            side_effect=[
                result(row=reservation),
                result(
                    row={
                        "company_id": company_id,
                        "budget_key": "max_cost_usd",
                        "limit_value": Decimal("10"),
                    }
                ),
                result(row=reservation),
                result(scalar=Decimal("9")),
                result(),
                result(row=refreshed),
            ]
        )

        settled = await storage.settle_budget_reservation(
            reservation_id,
            state="COMMITTED",
            amount=Decimal("5"),
        )

        assert settled == refreshed
        assert settled["amount"] == Decimal("1")
        assert settled["metadata"]["budget_settlement"] == "CAP_EXCEEDED"
        assert conn.execute.await_count == 6

    @pytest.mark.asyncio
    async def test_settle_budget_reservation_replay_is_a_noop(self):
        """A retry cannot commit or release a terminal reservation twice."""
        storage, engine = self._make_storage()
        conn = engine._mock_conn
        reservation_id = uuid4()
        company_id = uuid4()
        reservation = {
            "id": reservation_id,
            "company_id": company_id,
            "budget_key": "max_cost_usd",
            "amount": Decimal("2"),
            "state": "RESERVED",
            "metadata": {"source": "worker_dispatch"},
        }
        committed = {
            **reservation,
            "amount": Decimal("1"),
            "state": "COMMITTED",
            "metadata": {
                "source": "worker_dispatch",
                "actual_cost_usd": "1",
            },
        }

        def result(*, row=None, scalar=None):
            value = MagicMock()
            value.mappings.return_value.first.return_value = row
            value.scalar_one.return_value = scalar
            return value

        conn.execute = AsyncMock(
            side_effect=[
                result(row=reservation),
                result(
                    row={
                        "company_id": company_id,
                        "budget_key": "max_cost_usd",
                        "limit_value": Decimal("10"),
                    }
                ),
                result(row=reservation),
                result(scalar=Decimal("0")),
                result(),
                result(row=committed),
                result(row=committed),
            ]
        )

        first = await storage.settle_budget_reservation(
            reservation_id,
            state="COMMITTED",
            amount=Decimal("1"),
        )
        replay = await storage.settle_budget_reservation(
            reservation_id,
            state="COMMITTED",
            amount=Decimal("9"),
        )

        assert first == committed
        assert replay == committed
        assert conn.execute.await_count == 7

    # ── System Config ──

    @pytest.mark.asyncio
    async def test_get_config_missing(self):
        storage, engine = self._make_storage()
        conn = engine._mock_conn
        result_mock = MagicMock()
        result_mock.mappings.return_value = _mock_mappings([])
        conn.execute = AsyncMock(return_value=result_mock)

        val = await storage.get_config("nonexistent")
        assert val is None

    @pytest.mark.asyncio
    async def test_get_config_found(self):
        storage, engine = self._make_storage()
        conn = engine._mock_conn
        result_mock = MagicMock()
        result_mock.mappings.return_value = _mock_mappings(
            [
                {"key": "max_retries", "value": "5"},
            ]
        )
        conn.execute = AsyncMock(return_value=result_mock)

        val = await storage.get_config("max_retries")
        assert val == "5"

    @pytest.mark.asyncio
    async def test_get_all_config_empty(self):
        storage, engine = self._make_storage()
        conn = engine._mock_conn
        result_mock = MagicMock()
        result_mock.mappings.return_value = _mock_mappings([])
        conn.execute = AsyncMock(return_value=result_mock)

        cfg = await storage.get_all_config()
        assert cfg == {}

    # ── Engine guard ──

    def test_engine_property_raises_when_disconnected(self):
        storage = AgentStorage(dsn="postgresql+asyncpg://x:x@localhost/x")
        with pytest.raises(RuntimeError, match="not connected"):
            _ = storage.engine


@pytest.mark.asyncio
async def test_observe_agent_profile_clamps_database_numeric_bounds():
    storage = AgentStorage(dsn="postgresql+asyncpg://x:x@localhost/x")
    storage.get_agent_profile = AsyncMock(return_value=None)
    storage.upsert_agent_profile = AsyncMock(return_value={})

    await storage.observe_agent_profile(
        agent_id="worker-1",
        estimated_hours=1,
        actual_hours=100,
        alpha=1,
    )

    values = storage.upsert_agent_profile.await_args.kwargs
    assert values["correction_factor"] == Decimal("9.9999")
    assert values["estimation_bias"] == Decimal("9.9999")


# ===========================================================================
# TestCheckpointStore
# ===========================================================================


class TestCheckpointStore:
    """Test CheckpointStore methods with a mocked engine."""

    def _make_store(self) -> tuple[CheckpointStore, MagicMock]:
        engine = _make_mock_engine()
        store = CheckpointStore(engine)
        return store, engine

    @pytest.mark.asyncio
    async def test_save_returns_uuid(self):
        store, engine = self._make_store()
        conn = engine._mock_conn
        conn.execute = AsyncMock()

        cid = await store.save(
            agent_id="worker_1",
            team_id="dept_production",
            task_message_id="msg-001",
            iteration=3,
            messages_json=[{"role": "user", "content": "write code"}],
            task_envelope_json={"msg_type": "TASK"},
        )
        assert isinstance(cid, UUID)
        conn.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_save_with_explicit_id(self):
        store, engine = self._make_store()
        conn = engine._mock_conn
        conn.execute = AsyncMock()
        explicit_id = uuid4()

        cid = await store.save(
            agent_id="worker_2",
            team_id="dept_system",
            task_message_id="msg-002",
            iteration=0,
            messages_json=[],
            task_envelope_json={},
            checkpoint_id=explicit_id,
        )
        assert cid == explicit_id

    @pytest.mark.asyncio
    async def test_save_with_all_optional_fields(self):
        store, engine = self._make_store()
        conn = engine._mock_conn
        conn.execute = AsyncMock()

        cid = await store.save(
            agent_id="w1",
            team_id="t1",
            task_message_id="m1",
            iteration=5,
            messages_json=[{"role": "assistant", "content": "ok"}],
            project_id=uuid4(),
            tool_results_json=[{"tool": "search", "result": "found"}],
            budget_state_json={"llm_calls": 3, "remaining": 7},
            task_envelope_json={"msg_type": "TASK"},
        )
        assert isinstance(cid, UUID)

    @pytest.mark.asyncio
    async def test_load_returns_none_when_missing(self):
        store, engine = self._make_store()
        conn = engine._mock_conn
        result_mock = MagicMock()
        result_mock.mappings.return_value = _mock_mappings([])
        conn.execute = AsyncMock(return_value=result_mock)

        cp = await store.load("nonexistent_agent")
        assert cp is None

    @pytest.mark.asyncio
    async def test_load_returns_dict_when_found(self):
        store, engine = self._make_store()
        conn = engine._mock_conn
        row_data = {
            "id": uuid4(),
            "agent_id": "worker_1",
            "team_id": "dept_prod",
            "task_message_id": "msg-001",
            "iteration": 3,
            "messages_json": [{"role": "user", "content": "test"}],
        }
        result_mock = MagicMock()
        result_mock.mappings.return_value = _mock_mappings([row_data])
        conn.execute = AsyncMock(return_value=result_mock)

        cp = await store.load("worker_1", "msg-001")
        assert cp is not None
        assert cp["agent_id"] == "worker_1"
        assert cp["iteration"] == 3

    @pytest.mark.asyncio
    async def test_load_all_for_team_empty(self):
        store, engine = self._make_store()
        conn = engine._mock_conn
        result_mock = MagicMock()
        result_mock.mappings.return_value = _mock_mappings([])
        conn.execute = AsyncMock(return_value=result_mock)

        cps = await store.load_all_for_team("dept_unknown")
        assert cps == []

    @pytest.mark.asyncio
    async def test_load_latest_for_team_agents_uses_distinct_agent_query(self):
        from sqlalchemy.dialects import postgresql

        store, engine = self._make_store()
        conn = engine._mock_conn
        result_mock = MagicMock()
        result_mock.mappings.return_value = _mock_mappings([])
        conn.execute = AsyncMock(return_value=result_mock)

        cps = await store.load_latest_for_team_agents("dept_qa")

        assert cps == []
        query = conn.execute.await_args.args[0]
        sql = str(query.compile(dialect=postgresql.dialect()))
        assert "DISTINCT ON (agent_checkpoints.agent_id)" in sql
        assert "agent_checkpoints.saved_at DESC" in sql

    @pytest.mark.asyncio
    async def test_delete_returns_true_on_success(self):
        store, engine = self._make_store()
        conn = engine._mock_conn
        result_mock = MagicMock()
        result_mock.rowcount = 1
        conn.execute = AsyncMock(return_value=result_mock)

        deleted = await store.delete("worker_1", "msg-001")
        assert deleted is True

    @pytest.mark.asyncio
    async def test_delete_returns_false_when_not_found(self):
        store, engine = self._make_store()
        conn = engine._mock_conn
        result_mock = MagicMock()
        result_mock.rowcount = 0
        conn.execute = AsyncMock(return_value=result_mock)

        deleted = await store.delete("ghost", "msg-999")
        assert deleted is False

    @pytest.mark.asyncio
    async def test_delete_all_for_agent(self):
        store, engine = self._make_store()
        conn = engine._mock_conn
        result_mock = MagicMock()
        result_mock.rowcount = 3
        conn.execute = AsyncMock(return_value=result_mock)

        count = await store.delete_all_for_agent("worker_1")
        assert count == 3

    @pytest.mark.asyncio
    async def test_count(self):
        store, engine = self._make_store()
        conn = engine._mock_conn
        result_mock = MagicMock()
        result_mock.scalar.return_value = 7
        conn.execute = AsyncMock(return_value=result_mock)

        n = await store.count("worker_1")
        assert n == 7


# ===========================================================================
# TestBlobRef
# ===========================================================================


class TestBlobRef:
    """Test BlobRef dataclass serialisation."""

    def test_round_trip(self):
        ref = BlobRef(
            bucket="mas-agents",
            key="proj-123/documents/pdr_v1.json",
            sha256="abc123",
            size_bytes=1024,
            content_type="application/json",
        )
        d = ref.to_dict()
        assert d["bucket"] == "mas-agents"
        restored = BlobRef.from_dict(d)
        assert restored == ref

    def test_from_dict_default_content_type(self):
        d = {"bucket": "b", "key": "k", "sha256": "s", "size_bytes": 0}
        ref = BlobRef.from_dict(d)
        assert ref.content_type == "application/octet-stream"

    def test_frozen(self):
        ref = BlobRef(bucket="b", key="k", sha256="s", size_bytes=0)
        with pytest.raises(AttributeError):
            ref.bucket = "other"  # type: ignore


# ===========================================================================
# TestBlobClient
# ===========================================================================


class TestBlobClient:
    """Test BlobClient with mocked S3 (aioboto3)."""

    def _make_client(self) -> tuple[BlobClient, AsyncMock]:
        """Create a BlobClient with a pre-connected mock S3 client."""
        blob = BlobClient(
            endpoint_url="http://minio:9000",
            access_key="test",
            secret_key="test",
        )
        mock_s3 = AsyncMock()
        # Mock the exceptions for exists()
        mock_s3.exceptions = MagicMock()
        mock_s3.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})
        blob._client = mock_s3
        return blob, mock_s3

    @pytest.mark.asyncio
    async def test_upload_returns_blob_ref(self):
        blob, s3 = self._make_client()
        data = b'{"sections": ["intro"]}'
        sha = hashlib.sha256(data).hexdigest()

        ref = await blob.upload(
            "proj-123",
            "documents/pdr_v1.json",
            data,
            content_type="application/json",
        )

        assert isinstance(ref, BlobRef)
        assert ref.bucket == "mas-agents"
        assert ref.key == "proj-123/documents/pdr_v1.json"
        assert ref.sha256 == sha
        assert ref.size_bytes == len(data)
        assert ref.content_type == "application/json"
        s3.put_object.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_upload_custom_bucket(self):
        blob, s3 = self._make_client()
        ref = await blob.upload("proj-1", "file.txt", b"hi", bucket="custom-bucket")
        assert ref.bucket == "custom-bucket"

    @pytest.mark.asyncio
    async def test_download_integrity_check(self):
        blob, s3 = self._make_client()
        data = b"hello world"
        sha = hashlib.sha256(data).hexdigest()

        # Mock get_object response
        body_mock = AsyncMock()
        body_mock.read = AsyncMock(return_value=data)
        s3.get_object = AsyncMock(return_value={"Body": body_mock})

        ref = BlobRef(
            bucket="mas-agents",
            key="proj-123/test.txt",
            sha256=sha,
            size_bytes=len(data),
        )
        result = await blob.download(ref)
        assert result == data

    @pytest.mark.asyncio
    async def test_download_integrity_failure(self):
        blob, s3 = self._make_client()

        body_mock = AsyncMock()
        body_mock.read = AsyncMock(return_value=b"tampered data")
        s3.get_object = AsyncMock(return_value={"Body": body_mock})

        ref = BlobRef(
            bucket="mas-agents",
            key="proj-123/test.txt",
            sha256="wrong_hash",
            size_bytes=100,
        )
        with pytest.raises(ValueError, match="SHA-256 mismatch"):
            await blob.download(ref)

    @pytest.mark.asyncio
    async def test_download_by_key(self):
        blob, s3 = self._make_client()
        body_mock = AsyncMock()
        body_mock.read = AsyncMock(return_value=b"content")
        s3.get_object = AsyncMock(return_value={"Body": body_mock})

        data = await blob.download_by_key("proj-1", "file.txt")
        assert data == b"content"
        s3.get_object.assert_awaited_once_with(Bucket="mas-agents", Key="proj-1/file.txt")

    @pytest.mark.asyncio
    async def test_delete(self):
        blob, s3 = self._make_client()
        ref = BlobRef(bucket="mas-agents", key="proj-1/f.txt", sha256="x", size_bytes=0)
        await blob.delete(ref)
        s3.delete_object.assert_awaited_once_with(Bucket="mas-agents", Key="proj-1/f.txt")

    @pytest.mark.asyncio
    async def test_delete_by_key(self):
        blob, s3 = self._make_client()
        await blob.delete_by_key("proj-1", "file.txt")
        s3.delete_object.assert_awaited_once_with(Bucket="mas-agents", Key="proj-1/file.txt")

    @pytest.mark.asyncio
    async def test_list_objects(self):
        blob, s3 = self._make_client()
        s3.list_objects_v2 = AsyncMock(
            return_value={
                "Contents": [
                    {"Key": "proj-1/a.txt", "Size": 100, "LastModified": "2024-01-01T00:00:00Z"},
                    {"Key": "proj-1/b.txt", "Size": 200, "LastModified": "2024-01-02T00:00:00Z"},
                ]
            }
        )

        objects = await blob.list_objects("proj-1")
        assert len(objects) == 2
        assert objects[0]["key"] == "proj-1/a.txt"
        assert objects[1]["size"] == 200

    @pytest.mark.asyncio
    async def test_list_objects_empty(self):
        blob, s3 = self._make_client()
        s3.list_objects_v2 = AsyncMock(return_value={})

        objects = await blob.list_objects("proj-1")
        assert objects == []

    @pytest.mark.asyncio
    async def test_exists_true(self):
        blob, s3 = self._make_client()
        s3.head_object = AsyncMock(return_value={})

        assert await blob.exists("proj-1", "file.txt") is True

    @pytest.mark.asyncio
    async def test_exists_false(self):
        blob, s3 = self._make_client()
        s3.head_object = AsyncMock(side_effect=Exception("404"))

        assert await blob.exists("proj-1", "missing.txt") is False

    @pytest.mark.asyncio
    async def test_ensure_bucket_already_exists(self):
        blob, s3 = self._make_client()
        s3.head_bucket = AsyncMock(return_value={})

        await blob.ensure_bucket()
        s3.create_bucket.assert_not_called()

    @pytest.mark.asyncio
    async def test_ensure_bucket_creates(self):
        blob, s3 = self._make_client()
        s3.head_bucket = AsyncMock(side_effect=Exception("NoSuchBucket"))
        s3.create_bucket = AsyncMock()

        await blob.ensure_bucket()
        s3.create_bucket.assert_awaited_once_with(Bucket="mas-agents")

    def test_client_property_raises_before_connect(self):
        blob = BlobClient(
            endpoint_url="http://localhost:9000",
            access_key="a",
            secret_key="s",
        )
        with pytest.raises(RuntimeError, match="not connected"):
            _ = blob.client

    def test_full_key_format(self):
        blob = BlobClient(
            endpoint_url="http://localhost:9000",
            access_key="a",
            secret_key="s",
        )
        assert blob._full_key("proj-1", "docs/file.json") == "proj-1/docs/file.json"


# ===========================================================================
# TestMemoryInit
# ===========================================================================


class TestMemoryInit:
    """Verify the public API from mas_core.memory."""

    def test_public_exports(self):
        from mas_core.memory import (
            DEFAULT_RESOURCE_PROFILE_CONCURRENCY,
            DEFAULT_RESOURCE_PROFILE_PAYLOAD_SIZES,
            ENCRYPTION_ALGORITHM,
            MAX_MULTIPART_PARTS,
            MAX_MULTIPART_PAYLOAD_BYTES,
            MIN_PART_SIZE_BYTES,
            OBJECT_STORE_BACKUP_SCHEMA,
            OBJECT_STORE_CONFORMANCE_SCHEMA,
            OBJECT_STORE_COPY_SCHEMA,
            OBJECT_STORE_ENCRYPTED_BACKUP_SCHEMA,
            OBJECT_STORE_ENCRYPTED_RESTORE_SCHEMA,
            OBJECT_STORE_MULTIPART_SCHEMA,
            OBJECT_STORE_RESOURCE_PROFILE_SCHEMA,
            OBJECT_STORE_RESTORE_SCHEMA,
            AgentStorage,
            BackupManifest,
            BackupObject,
            BlobClient,
            BlobRef,
            CheckpointStore,
            EncryptedBackupManifest,
            EncryptedBackupObject,
            EncryptedRestoreVerification,
            InMemoryObjectStore,
            MultipartObjectStoreAdapter,
            MultipartUploadConfig,
            MultipartUploadReport,
            ObjectStoreAdapter,
            ObjectStoreConformanceCase,
            ObjectStoreConformanceReport,
            ObjectStoreCopyCase,
            ObjectStoreCopyReport,
            ObjectStoreResourceProfileConfig,
            ObjectStoreResourceProfileReport,
            RestoreVerification,
            assert_clean_restore_target,
            build_backup_manifest,
            build_encrypted_backup,
            copy_manifest_objects,
            metadata,
            replicate_encrypted_backup,
            run_object_store_conformance,
            run_object_store_multipart_probe,
            run_object_store_resource_profile,
            verify_and_copy_blobs,
            verify_encrypted_backup,
            verify_restored_manifest,
        )

        assert AgentStorage is not None
        assert BlobClient is not None
        assert BlobRef is not None
        assert CheckpointStore is not None
        assert OBJECT_STORE_CONFORMANCE_SCHEMA
        assert InMemoryObjectStore is not None
        assert ObjectStoreAdapter is not None
        assert ObjectStoreConformanceCase is not None
        assert ObjectStoreConformanceReport is not None
        assert run_object_store_conformance is not None
        assert OBJECT_STORE_COPY_SCHEMA
        assert ObjectStoreCopyCase is not None
        assert ObjectStoreCopyReport is not None
        assert verify_and_copy_blobs is not None
        assert OBJECT_STORE_BACKUP_SCHEMA
        assert OBJECT_STORE_RESTORE_SCHEMA
        assert BackupManifest is not None
        assert BackupObject is not None
        assert RestoreVerification is not None
        assert assert_clean_restore_target is not None
        assert build_backup_manifest is not None
        assert copy_manifest_objects is not None
        assert verify_restored_manifest is not None
        assert metadata is not None
        assert DEFAULT_RESOURCE_PROFILE_CONCURRENCY > 0
        assert DEFAULT_RESOURCE_PROFILE_PAYLOAD_SIZES
        assert MAX_MULTIPART_PARTS > 0
        assert MAX_MULTIPART_PAYLOAD_BYTES > 0
        assert MIN_PART_SIZE_BYTES > 0
        assert ENCRYPTION_ALGORITHM
        assert OBJECT_STORE_MULTIPART_SCHEMA
        assert OBJECT_STORE_RESOURCE_PROFILE_SCHEMA
        assert OBJECT_STORE_ENCRYPTED_BACKUP_SCHEMA
        assert OBJECT_STORE_ENCRYPTED_RESTORE_SCHEMA
        assert MultipartObjectStoreAdapter is not None
        assert MultipartUploadConfig is not None
        assert MultipartUploadReport is not None
        assert run_object_store_multipart_probe is not None
        assert ObjectStoreResourceProfileConfig is not None
        assert ObjectStoreResourceProfileReport is not None
        assert run_object_store_resource_profile is not None
        assert EncryptedBackupManifest is not None
        assert EncryptedBackupObject is not None
        assert EncryptedRestoreVerification is not None
        assert build_encrypted_backup is not None
        assert replicate_encrypted_backup is not None
        assert verify_encrypted_backup is not None

    def test_all_list(self):
        import mas_core.memory as mem

        expected = {
            "AgentStorage",
            "BlobClient",
            "BlobRef",
            "CheckpointStore",
            "OBJECT_STORE_CONFORMANCE_SCHEMA",
            "InMemoryObjectStore",
            "ObjectStoreAdapter",
            "ObjectStoreConformanceCase",
            "ObjectStoreConformanceReport",
            "run_object_store_conformance",
            "OBJECT_STORE_COPY_SCHEMA",
            "ObjectStoreCopyCase",
            "ObjectStoreCopyReport",
            "verify_and_copy_blobs",
            "MAX_MULTIPART_PARTS",
            "MAX_MULTIPART_PAYLOAD_BYTES",
            "MIN_PART_SIZE_BYTES",
            "OBJECT_STORE_MULTIPART_SCHEMA",
            "MultipartObjectStoreAdapter",
            "MultipartUploadConfig",
            "MultipartUploadReport",
            "run_object_store_multipart_probe",
            "DEFAULT_RESOURCE_PROFILE_CONCURRENCY",
            "DEFAULT_RESOURCE_PROFILE_PAYLOAD_SIZES",
            "OBJECT_STORE_RESOURCE_PROFILE_SCHEMA",
            "ObjectStoreResourceProfileConfig",
            "ObjectStoreResourceProfileReport",
            "run_object_store_resource_profile",
            "ENCRYPTION_ALGORITHM",
            "OBJECT_STORE_ENCRYPTED_BACKUP_SCHEMA",
            "OBJECT_STORE_ENCRYPTED_RESTORE_SCHEMA",
            "EncryptedBackupManifest",
            "EncryptedBackupObject",
            "EncryptedRestoreVerification",
            "build_encrypted_backup",
            "replicate_encrypted_backup",
            "verify_encrypted_backup",
            "OBJECT_STORE_BACKUP_SCHEMA",
            "OBJECT_STORE_RESTORE_SCHEMA",
            "BackupManifest",
            "BackupObject",
            "RestoreVerification",
            "assert_clean_restore_target",
            "build_backup_manifest",
            "copy_manifest_objects",
            "verify_restored_manifest",
            "OBJECT_STORE_MIGRATION_SCHEMA",
            "DualWriteRecord",
            "MigrationActorKind",
            "MigrationStatus",
            "MigrationTransition",
            "ObjectStoreMigrationError",
            "ObjectStoreMigrationWorkflow",
            "MAX_LIFECYCLE_KEY_LENGTH",
            "MAX_LIFECYCLE_OBJECTS",
            "OBJECT_STORE_HOLD_SNAPSHOT_SCHEMA",
            "OBJECT_STORE_LIFECYCLE_SCHEMA",
            "LegalHoldSnapshot",
            "LifecycleCanonicalObject",
            "LifecycleInventoryObject",
            "ObjectLifecycleDeleteAdapter",
            "ObjectLifecycleError",
            "ObjectLifecycleExecution",
            "ObjectLifecyclePlan",
            "execute_object_lifecycle",
            "plan_object_lifecycle",
            "OCI_OBJECT_STORE_SCHEMA",
            "OCIEncryptionEvidenceError",
            "OCIObjectStorageSdkTransport",
            "OCIObjectStorageTransport",
            "OCIObjectStoreAdapter",
            "OCIObjectStoreConfig",
            "OCIProviderUnavailable",
            "OCITransportError",
            "FakeOCIObjectStorageTransport",
            "run_oci_sse_kms_probe",
            "OPTIONAL_MEMORY_ADAPTER_SCHEMA",
            "QDRANT_ADAPTER_SCHEMA",
            "TEMPORAL_ADAPTER_SCHEMA",
            "OptionalServiceContractError",
            "OptionalServiceHealth",
            "OptionalServiceUnavailable",
            "QdrantBackend",
            "QdrantVectorAdapter",
            "TemporalBackend",
            "TemporalWorkflowAdapter",
            "VectorDeleteResult",
            "VectorPoint",
            "VectorSearchHit",
            "VectorWriteResult",
            "WorkflowCommand",
            "WorkflowRunReference",
            "metadata",
        }
        assert set(mem.__all__) == expected
