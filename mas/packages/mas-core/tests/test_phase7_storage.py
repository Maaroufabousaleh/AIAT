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
    dead_letters,
    documents,
    infra_events,
    issues,
    kpi_snapshots,
    metadata,
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
    """Verify that all canonical 20 tables are properly defined in metadata."""

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
        "agent_checkpoints",
        "memory",
        "task_log",
        "artifacts",
        "infra_events",
        "capabilities",
        "worker_registry",
        "role_capability_map",
        "evaluation_reports",
        "flows",
        "flow_instances",
        "flow_node_executions",
    ]

    def test_all_28_tables_present(self):
        """metadata.tables should contain exactly 28 table names."""
        assert len(metadata.tables) == 28
        for name in self.EXPECTED_TABLES:
            assert name in metadata.tables, f"Missing table: {name}"

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
            "name",
            "description",
            "state",
            "failure_reason",
            "failed_from_state",
            "created_by",
            "human_requester",
            "config",
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
        conn.execute.assert_awaited_once()

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
        conn.execute = AsyncMock()

        await storage.decide_approval_gate(
            uuid4(),
            status="APPROVED",
            decided_by="human_operator",
            justification="Looks good",
        )
        conn.execute.assert_awaited_once()

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
        conn.execute.assert_awaited_once()

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
            AgentStorage,
            BlobClient,
            BlobRef,
            CheckpointStore,
            metadata,
        )

        assert AgentStorage is not None
        assert BlobClient is not None
        assert BlobRef is not None
        assert CheckpointStore is not None
        assert metadata is not None

    def test_all_list(self):
        import mas_core.memory as mem

        expected = {"AgentStorage", "BlobClient", "BlobRef", "CheckpointStore", "metadata"}
        assert set(mem.__all__) == expected
