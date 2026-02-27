"""Unit tests for mas_core.protocols — Phase 1 verification.

Covers:
- MessageEnvelope construction and validation
- Payload size enforcement (MAX_PAYLOAD_BYTES)
- BlobRef, TaskBudget models
- MessageType and AgentRole enum completeness
- ToolRequest / ToolResponse models
- Domain models: ProjectDocument, ReviewSummary circuit breaker,
  FeasibilityReport.is_viable, KPISnapshot, AgentProfile
- reply() helper wires correlation IDs correctly
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from mas_core.protocols import (
    MAX_PAYLOAD_BYTES,
    AgentProfile,
    AgentRole,
    BlobRef,
    CircuitState,
    DocumentState,
    DocumentType,
    FailureReason,
    FeasibilityReport,
    HumanDecision,
    Issue,
    IssuePriority,
    IssueStatus,
    IssueType,
    KPIMetricType,
    KPISnapshot,
    MessageEnvelope,
    MessageType,
    Milestone,
    ProjectDocument,
    ProjectState,
    ProjectSummary,
    ReviewComment,
    ReviewResponse,
    ReviewSession,
    ReviewSessionStatus,
    ReviewSeverity,
    ReviewSummary,
    ReviewVerdict,
    Sprint,
    SprintStatus,
    TaskBudget,
    ToolManifestEntry,
    ToolRequest,
    ToolResponse,
    WSAckFrame,
    WSMessageFrame,
    WSNackFrame,
    WSPingFrame,
    WSPongFrame,
    parse_agent_frame,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _basic_envelope(**kwargs) -> MessageEnvelope:
    defaults = dict(
        msg_type=MessageType.TASK,
        sender_id="ceo_agent",
        sender_role=AgentRole.ORCHESTRATOR,
        sender_team="exec_ceo",
        recipient_id="coo_agent",
        project_id="proj-001",
    )
    defaults.update(kwargs)
    return MessageEnvelope(**defaults)


# ---------------------------------------------------------------------------
# MessageEnvelope — basic construction
# ---------------------------------------------------------------------------

class TestMessageEnvelopeBasic:
    def test_defaults_are_sane(self):
        env = _basic_envelope()
        assert env.ttl_seconds == 3600
        assert env.retry_count == 0
        assert env.ack_required is True
        assert env.payload == {}
        assert env.blob_ref is None
        assert env.budget is None

    def test_message_id_auto_generated(self):
        a = _basic_envelope()
        b = _basic_envelope()
        assert a.message_id != b.message_id

    def test_timestamp_is_utc(self):
        env = _basic_envelope()
        assert env.timestamp.tzinfo is not None

    def test_recipient_team_accepted(self):
        env = _basic_envelope(recipient_id=None, recipient_team="dept_production")
        assert env.recipient_team == "dept_production"


# ---------------------------------------------------------------------------
# MessageEnvelope — routing validation
# ---------------------------------------------------------------------------

class TestMessageEnvelopeRouting:
    def test_sender_team_required(self):
        with pytest.raises(ValueError, match="sender_team"):
            MessageEnvelope(
                msg_type=MessageType.TASK,
                sender_id="a",
                sender_role=AgentRole.ORCHESTRATOR,
                recipient_id="b",
                project_id="proj-001",
            )

    def test_no_recipient_raises(self):
        with pytest.raises(ValueError, match="recipient"):
            MessageEnvelope(
                msg_type=MessageType.TASK,
                sender_id="a",
                sender_role=AgentRole.ORCHESTRATOR,
                sender_team="exec_ceo",
                # neither recipient_id nor recipient_team set
                project_id="proj-001",
            )

    def test_heartbeat_exempt_from_recipient_check(self):
        env = MessageEnvelope(
            msg_type=MessageType.HEARTBEAT,
            sender_id="router",
            sender_role=AgentRole.ORCHESTRATOR,
            sender_team="exec_ceo",
        )
        assert env.msg_type == MessageType.HEARTBEAT

    def test_ack_exempt_from_recipient_check(self):
        env = MessageEnvelope(
            msg_type=MessageType.ACK,
            sender_id="router",
            sender_role=AgentRole.WORKER,
            sender_team="dept_system",
        )
        assert env.msg_type == MessageType.ACK

    def test_project_id_required_for_task_messages(self):
        with pytest.raises(ValueError, match="project_id"):
            MessageEnvelope(
                msg_type=MessageType.TASK,
                sender_id="ceo_agent",
                sender_role=AgentRole.ORCHESTRATOR,
                sender_team="exec_ceo",
                recipient_team="exec_coo",
            )

    def test_project_id_not_required_for_shutdown(self):
        env = MessageEnvelope(
            msg_type=MessageType.SHUTDOWN,
            sender_id="orchestrator",
            sender_role=AgentRole.ORCHESTRATOR,
            sender_team="exec_ceo",
            recipient_team="dept_system",
        )
        assert env.project_id is None

    def test_rejects_both_recipient_id_and_team_for_non_broadcast(self):
        with pytest.raises(ValueError, match="either 'recipient_id' or 'recipient_team'"):
            _basic_envelope(recipient_team="exec_coo")


# ---------------------------------------------------------------------------
# MessageEnvelope — payload size enforcement
# ---------------------------------------------------------------------------

class TestPayloadSizeEnforcement:
    def test_small_payload_accepted(self):
        env = _basic_envelope(payload={"key": "value"})
        assert env.payload == {"key": "value"}

    def test_payload_exactly_at_limit_accepted(self):
        # Build a payload that serialises to exactly MAX_PAYLOAD_BYTES
        # Use a key + value that just fits
        value = "x" * (MAX_PAYLOAD_BYTES - len(b'{"k": ""}') )
        payload = {"k": value}
        serialised = json.dumps(payload, default=str).encode()
        assert len(serialised) <= MAX_PAYLOAD_BYTES
        env = _basic_envelope(payload=payload)
        assert env.payload == payload

    def test_oversized_payload_raises(self):
        big = {"data": "a" * (MAX_PAYLOAD_BYTES + 1)}
        with pytest.raises(ValueError, match="MAX_PAYLOAD_BYTES"):
            _basic_envelope(payload=big)


# ---------------------------------------------------------------------------
# BlobRef
# ---------------------------------------------------------------------------

class TestBlobRef:
    def test_construction(self):
        ref = BlobRef(
            bucket="mas-agents",
            key="proj-001/documents/pdr_v1.json",
            sha256="abc123" * 10,
            size_bytes=1_000_000,
        )
        assert ref.bucket == "mas-agents"
        assert ref.size_bytes == 1_000_000

    def test_negative_size_rejected(self):
        with pytest.raises(ValueError):
            BlobRef(bucket="b", key="k", sha256="h", size_bytes=-1)


# ---------------------------------------------------------------------------
# TaskBudget
# ---------------------------------------------------------------------------

class TestTaskBudget:
    def test_uncapped_always_has_budget(self):
        b = TaskBudget()
        assert b.llm_budget_remaining() is True
        assert b.tool_budget_remaining() is True
        assert b.deadline_exceeded() is False

    def test_deadline_exceeded(self):
        past = datetime.now(tz=timezone.utc) - timedelta(seconds=1)
        b = TaskBudget(deadline=past)
        assert b.deadline_exceeded() is True

    def test_deadline_not_exceeded(self):
        future = datetime.now(tz=timezone.utc) + timedelta(hours=1)
        b = TaskBudget(deadline=future)
        assert b.deadline_exceeded() is False

    def test_budget_only_on_task_messages(self):
        budget = TaskBudget(max_llm_calls=5)
        with pytest.raises(ValueError, match="budget"):
            MessageEnvelope(
                msg_type=MessageType.RESPONSE,
                sender_id="a",
                sender_role=AgentRole.WORKER,
                sender_team="dept_system",
                recipient_id="b",
                project_id="proj-001",
                budget=budget,
            )

    def test_budget_accepted_on_task(self):
        budget = TaskBudget(max_llm_calls=5)
        env = _basic_envelope(budget=budget)
        assert env.budget.max_llm_calls == 5


# ---------------------------------------------------------------------------
# reply() helper
# ---------------------------------------------------------------------------

class TestReplyHelper:
    def test_reply_wires_correlation_and_parent(self):
        original = _basic_envelope()
        reply = original.reply(
            MessageType.RESULT,
            payload={"status": "done"},
            sender_id="coo_agent",
            sender_role=AgentRole.EXECUTIVE,
            sender_team="exec_coo",
        )
        assert reply.parent_id == original.message_id
        assert reply.correlation_id == original.message_id
        assert reply.recipient_id == original.sender_id
        assert reply.project_id == original.project_id

    def test_reply_preserves_existing_correlation_id(self):
        cid = uuid4()
        original = _basic_envelope(correlation_id=cid)
        reply = original.reply(
            MessageType.RESULT,
            sender_id="coo_agent",
            sender_role=AgentRole.EXECUTIVE,
            sender_team="exec_coo",
        )
        assert reply.correlation_id == cid


# ---------------------------------------------------------------------------
# MessageEnvelope.is_expired()
# ---------------------------------------------------------------------------

class TestIsExpired:
    def test_fresh_message_not_expired(self):
        env = _basic_envelope(ttl_seconds=3600)
        assert env.is_expired() is False

    def test_old_message_expired(self):
        past = datetime.now(tz=timezone.utc) - timedelta(seconds=7200)
        env = _basic_envelope(ttl_seconds=3600)
        object.__setattr__(env, "timestamp", past)
        assert env.is_expired() is True


# ---------------------------------------------------------------------------
# Enum completeness
# ---------------------------------------------------------------------------

class TestEnumCompleteness:
    def test_all_message_types_present(self):
        expected = {
            "TASK", "RESULT", "QUERY", "RESPONSE", "BROADCAST",
            "ADMIN_TASK", "ADMIN_REPLY", "SHUTDOWN", "SHUTDOWN_ACK",
            "DOCUMENT_SUBMIT", "DOCUMENT_REVISION",
            "REVIEW_REQUEST", "REVIEW_RESPONSE",
            "APPROVAL_REQUEST", "APPROVAL_RESPONSE",
            "SPRINT_PLAN", "SPRINT_REPORT", "ISSUE_ASSIGN", "ISSUE_COMPLETE",
            "ESCALATION", "DIRECTIVE",
            "INFRA_READY",
            "HEARTBEAT", "ACK", "SYSTEM_EVENT",
        }
        actual = {m.name for m in MessageType}
        assert expected.issubset(actual), f"Missing message types: {expected - actual}"

    def test_all_agent_roles_present(self):
        expected = {"ORCHESTRATOR", "EXECUTIVE", "C_SUITE", "ADMIN", "WORKER", "SUB_AGENT"}
        actual = {r.name for r in AgentRole}
        assert expected == actual

    def test_sprint_status_completeness(self):
        expected = {"PLANNED", "ACTIVE", "COMPLETED", "CANCELLED"}
        actual = {s.name for s in SprintStatus}
        assert expected == actual

    def test_kpi_metric_type_completeness(self):
        expected = {
            "ESTIMATION_ACCURACY", "TASK_COMPLETION_RATE", "REVIEW_PASS_RATE",
            "VELOCITY", "DEFECT_RATE", "REWORK_RATE",
            "BUDGET_ADHERENCE", "RESOURCE_UTILIZATION", "INFRA_LEAD_TIME",
        }
        actual = {k.name for k in KPIMetricType}
        assert expected == actual

    def test_document_state_completeness(self):
        expected = {
            "DRAFT", "IN_REVIEW", "APPROVED", "REJECTED",
            "NEEDS_REVISION", "SUPERSEDED", "ARCHIVED",
        }
        actual = {s.name for s in DocumentState}
        assert expected == actual

    def test_failure_reason_completeness(self):
        expected = {
            "WATCHDOG_TIMEOUT", "REVIEW_CIRCUIT_OPEN", "DLQ_OVERFLOW",
            "INFRA_FAILURE", "AGENT_BUDGET_EXHAUSTED", "UNRECOVERABLE_ERROR",
        }
        actual = {r.name for r in FailureReason}
        assert expected == actual

    def test_issue_type_completeness(self):
        expected = {"FEATURE", "TEST", "QA", "DOCS", "INFRA", "BUGFIX", "BUG", "REWORK"}
        actual = {t.name for t in IssueType}
        assert expected == actual

    def test_issue_priority_completeness(self):
        expected = {"CRITICAL", "HIGH", "MEDIUM", "LOW"}
        actual = {p.name for p in IssuePriority}
        assert expected == actual

    def test_issue_status_completeness(self):
        expected = {"BACKLOG", "IN_PROGRESS", "IN_REVIEW", "DONE", "BLOCKED", "CANCELLED"}
        actual = {s.name for s in IssueStatus}
        assert expected == actual


# ---------------------------------------------------------------------------
# ToolRequest / ToolResponse
# ---------------------------------------------------------------------------

class TestToolModels:
    def test_tool_request_construction(self):
        req = ToolRequest(
            caller_id="ceo_agent",
            caller_role=AgentRole.ORCHESTRATOR,
            project_id="proj-001",
            tool_name="project.transition",
            tool_kwargs={"project_id": "proj-001", "event": "project_created"},
        )
        assert req.tool_name == "project.transition"
        assert req.idempotency_key is None

    def test_tool_request_aliases_from_tool_service_contract(self):
        req = ToolRequest(
            agent_id="ceo_agent",
            sender_role=AgentRole.ORCHESTRATOR,
            project_id="proj-001",
            tool_name="project.transition",
            kwargs={"event": "project_created"},
        )
        assert req.caller_id == "ceo_agent"
        assert req.caller_role == AgentRole.ORCHESTRATOR
        assert req.tool_kwargs == {"event": "project_created"}

    def test_tool_response_success(self):
        resp = ToolResponse(
            tool_name="web.search",
            success=True,
            result={"hits": []},
            circuit_state=CircuitState.CLOSED,
        )
        assert resp.success is True
        assert resp.cached is False

    def test_tool_response_failure(self):
        resp = ToolResponse(
            tool_name="sprint.create",
            success=False,
            error="Forbidden",
            error_code="FORBIDDEN",
            circuit_state=CircuitState.CLOSED,
        )
        assert resp.error_code == "FORBIDDEN"

    def test_tool_manifest_entry(self):
        entry = ToolManifestEntry(
            tool_name="project.transition",
            tool_group="project",
            description="Trigger a project state machine transition.",
            allowed_roles=[AgentRole.ORCHESTRATOR, AgentRole.EXECUTIVE],
            blocked_roles=[AgentRole.WORKER, AgentRole.SUB_AGENT],
        )
        assert AgentRole.WORKER in entry.blocked_roles


# ---------------------------------------------------------------------------
# Domain models
# ---------------------------------------------------------------------------

class TestProjectDocument:
    def test_defaults(self):
        doc = ProjectDocument(
            project_id="p1",
            doc_type=DocumentType.PDR,
            created_by="production_pm",
            team_id="dept_production",
        )
        assert doc.version == 1
        assert doc.revision_count == 0
        assert doc.sections == {}
        assert doc.content_ref is None


class TestReviewSummary:
    def test_circuit_open_at_two_timeouts(self):
        summary = ReviewSummary(
            project_id="p1",
            document_id=uuid4(),
            doc_type=DocumentType.PDR,
            reviewer_count=4,
            timeout_count=2,
        )
        assert summary.circuit_open is True

    def test_circuit_not_open_at_one_timeout(self):
        summary = ReviewSummary(
            project_id="p1",
            document_id=uuid4(),
            doc_type=DocumentType.PDR,
            reviewer_count=4,
            timeout_count=1,
        )
        assert summary.circuit_open is False

    def test_is_complete_when_all_respond(self):
        summary = ReviewSummary(
            project_id="p1",
            document_id=uuid4(),
            doc_type=DocumentType.PDR,
            reviewer_count=4,
            responses_received=4,
        )
        assert summary.is_complete is True

    def test_not_complete_when_reviewer_count_not_initialized(self):
        summary = ReviewSummary(
            project_id="p1",
            document_id=uuid4(),
            doc_type=DocumentType.PDR,
            reviewer_count=0,
            responses_received=0,
        )
        assert summary.is_complete is False


class TestFeasibilityReport:
    def test_viable_when_all_pass(self):
        report = FeasibilityReport(
            project_id="p1",
            financial_viable=True,
            technical_viable=True,
            resource_viable=True,
            security_viable=True,
            assembled_by="ceo_agent",
        )
        assert report.is_viable is True

    def test_not_viable_when_one_fails(self):
        report = FeasibilityReport(
            project_id="p1",
            financial_viable=True,
            technical_viable=False,
            resource_viable=True,
            security_viable=True,
            assembled_by="ceo_agent",
        )
        assert report.is_viable is False

    def test_not_viable_when_incomplete(self):
        report = FeasibilityReport(project_id="p1", assembled_by="ceo_agent")
        assert report.is_viable is False


class TestKPISnapshot:
    def test_estimation_accuracy_bounds(self):
        with pytest.raises(ValueError):
            KPISnapshot(project_id="p1", estimation_accuracy=1.5)  # > 1.0

    def test_valid_snapshot(self):
        snap = KPISnapshot(
            project_id="p1",
            estimation_accuracy=0.85,
            velocity=3.2,
            infra_lead_time_minutes=42.0,
        )
        assert snap.infra_lead_time_minutes == 42.0

    def test_metric_type_supports_infra_and_resource_metrics(self):
        infra = KPISnapshot(project_id="p1", metric_type="INFRA_LEAD_TIME", value=120.0)
        resource = KPISnapshot(project_id="p1", metric_type="RESOURCE_UTILIZATION", value=0.72)
        assert infra.metric_type == "INFRA_LEAD_TIME"
        assert resource.metric_type == "RESOURCE_UTILIZATION"


class TestAgentProfile:
    def test_correction_factor_bounds(self):
        with pytest.raises(ValueError):
            AgentProfile(agent_id="a", team_id="t", correction_factor=0.0)  # < 0.1

    def test_defaults(self):
        profile = AgentProfile(agent_id="worker_1", team_id="dept_production")
        assert profile.correction_factor == 1.0
        assert profile.confidence == 0.5
        assert profile.tasks_completed == 0


class TestSprint:
    def test_infra_block_default(self):
        sprint = Sprint(project_id="p1", sprint_number=1, name="Sprint 1")
        assert sprint.infra_ready is False
        assert sprint.blocked_until_infra is False


class TestIssue:
    def test_issue_construction(self):
        issue = Issue(
            project_id="p1",
            title="Provision dev environment",
            issue_type=IssueType.INFRA,
        )
        assert issue.status.value == "backlog"
        assert issue.actual_hours is None


# ---------------------------------------------------------------------------
# ProjectState enum
# ---------------------------------------------------------------------------


class TestProjectState:
    def test_all_states_present(self):
        expected = {
            "INIT", "FEASIBILITY_CHECK", "FEASIBILITY_REPORT",
            "PDR_CREATION", "PDR_REVIEW", "SECURITY_BLOCKED",
            "CDR_CREATION", "CDR_REVIEW", "HUMAN_APPROVAL",
            "RR_CREATION", "SPRINT_PLANNING", "INFRA_PROVISIONING",
            "IN_PROGRESS", "RETROSPECTIVE", "KPI_PERSISTENCE",
            "COMPLETED", "ARCHIVED", "FAILED",
        }
        actual = {s.name for s in ProjectState}
        assert expected == actual, f"Missing: {expected - actual}; Extra: {actual - expected}"

    def test_terminal_states(self):
        terminals = {ProjectState.COMPLETED, ProjectState.ARCHIVED, ProjectState.FAILED}
        assert ProjectState.FAILED in terminals
        assert ProjectState.COMPLETED in terminals

    def test_string_serialisation(self):
        assert ProjectState.IN_PROGRESS.value == "IN_PROGRESS"
        assert ProjectState.FEASIBILITY_CHECK.value == "FEASIBILITY_CHECK"


# ---------------------------------------------------------------------------
# ReviewSessionStatus enum
# ---------------------------------------------------------------------------


class TestReviewSessionStatus:
    def test_all_states_present(self):
        expected = {"IN_PROGRESS", "COMPLETED", "TIMED_OUT", "CIRCUIT_OPEN"}
        actual = {s.name for s in ReviewSessionStatus}
        assert expected == actual


# ---------------------------------------------------------------------------
# ReviewSession domain model
# ---------------------------------------------------------------------------


class TestReviewSession:
    def test_defaults(self):
        session = ReviewSession(
            project_id="p1",
            document_id=uuid4(),
            doc_type=DocumentType.PDR,
            reviewer_ids=["cfo_agent", "cio_agent", "chrm_agent", "cso_agent"],
            reviewer_count=4,
        )
        assert session.status == ReviewSessionStatus.IN_PROGRESS
        assert session.responses_received == 0
        assert session.timeout_count == 0
        assert session.circuit_open is False
        assert session.is_complete is False
        assert session.cso_veto is False

    def test_circuit_open_at_two_timeouts(self):
        session = ReviewSession(
            project_id="p1",
            document_id=uuid4(),
            doc_type=DocumentType.PDR,
            reviewer_count=4,
            timeout_count=2,
        )
        assert session.circuit_open is True

    def test_is_complete_when_all_respond(self):
        session = ReviewSession(
            project_id="p1",
            document_id=uuid4(),
            doc_type=DocumentType.PDR,
            reviewer_count=3,
            responses_received=3,
        )
        assert session.is_complete is True

    def test_cso_veto_tracked(self):
        session = ReviewSession(
            project_id="p1",
            document_id=uuid4(),
            doc_type=DocumentType.PDR,
            reviewer_count=4,
            cso_veto=True,
            cso_veto_comment="Critical authentication bypass discovered.",
        )
        assert session.cso_veto is True
        assert "authentication" in session.cso_veto_comment

    def test_not_complete_when_reviewer_count_not_initialized(self):
        session = ReviewSession(
            project_id="p1",
            document_id=uuid4(),
            doc_type=DocumentType.PDR,
            reviewer_count=0,
            responses_received=0,
        )
        assert session.is_complete is False


# ---------------------------------------------------------------------------
# Milestone domain model
# ---------------------------------------------------------------------------


class TestMilestone:
    def test_defaults(self):
        m = Milestone(project_id="p1", name="MVP Release")
        assert m.order == 1
        assert m.completed is False
        assert m.sprint_ids == []
        assert m.acceptance_criteria == []

    def test_ordering(self):
        m1 = Milestone(project_id="p1", name="Alpha", order=1)
        m2 = Milestone(project_id="p1", name="Beta", order=2)
        assert m1.order < m2.order

    def test_sprint_ids_populated(self):
        sid1, sid2 = uuid4(), uuid4()
        m = Milestone(
            project_id="p1",
            name="Sprint Block 1",
            sprint_ids=[sid1, sid2],
            acceptance_criteria=["All unit tests pass", "API coverage >= 90%"],
        )
        assert len(m.sprint_ids) == 2
        assert len(m.acceptance_criteria) == 2


# ---------------------------------------------------------------------------
# ProjectSummary domain model
# ---------------------------------------------------------------------------


class TestProjectSummary:
    def test_defaults(self):
        ps = ProjectSummary(
            project_id="proj-001",
            name="Build AIAT",
            requested_by="human_user",
        )
        assert ps.state == ProjectState.INIT
        assert ps.failure_reason is None
        assert ps.failed_from_state is None

    def test_failed_state_captures_reason(self):
        ps = ProjectSummary(
            project_id="proj-001",
            name="Build AIAT",
            requested_by="human_user",
            state=ProjectState.FAILED,
            failure_reason="REVIEW_CIRCUIT_OPEN",
            failed_from_state=ProjectState.PDR_REVIEW,
        )
        assert ps.state == ProjectState.FAILED
        assert ps.failed_from_state == ProjectState.PDR_REVIEW


# ---------------------------------------------------------------------------
# TaskBudget — PrivateAttr instance isolation
# ---------------------------------------------------------------------------


class TestTaskBudgetPrivateAttr:
    def test_counters_are_instance_isolated(self):
        b1 = TaskBudget(max_llm_calls=5)
        b2 = TaskBudget(max_llm_calls=5)
        # Mutating b1 must not affect b2
        b1._llm_calls_used = 3
        assert b2._llm_calls_used == 0

    def test_counters_not_serialised(self):
        b = TaskBudget(max_llm_calls=5)
        b._llm_calls_used = 2
        dumped = b.model_dump()
        assert "_llm_calls_used" not in dumped
        assert "_tool_calls_used" not in dumped


# ---------------------------------------------------------------------------
# WS frame models
# ---------------------------------------------------------------------------


class TestWSFrames:
    def _make_envelope(self) -> MessageEnvelope:
        return _basic_envelope()

    def test_message_frame_construction(self):
        env = self._make_envelope()
        frame = WSMessageFrame(
            entry_id="1708900000000-0",
            envelope=env,
            stream="stream:exec_ceo",
        )
        assert frame.type == "MESSAGE"
        assert frame.entry_id == "1708900000000-0"
        assert frame.retry_count == 0
        assert frame.envelope.message_id == env.message_id

    def test_ping_frame_defaults(self):
        ping = WSPingFrame()
        assert ping.type == "PING"
        assert ping.ping_id != ""

    def test_ack_frame_construction(self):
        ack = WSAckFrame(entry_id="1708900000000-0")
        assert ack.type == "ACK"
        assert ack.entry_id == "1708900000000-0"

    def test_nack_frame_construction(self):
        nack = WSNackFrame(entry_id="1708900000000-0", reason="resource busy")
        assert nack.type == "NACK"
        assert nack.reason == "resource busy"

    def test_pong_frame_construction(self):
        pong = WSPongFrame(ping_id="1708900000000", agent_id="ceo_agent")
        assert pong.type == "PONG"
        assert pong.agent_id == "ceo_agent"

    def test_parse_agent_frame_ack(self):
        raw = {"type": "ACK", "entry_id": "1708900000000-0"}
        frame = parse_agent_frame(raw)
        assert isinstance(frame, WSAckFrame)

    def test_parse_agent_frame_nack(self):
        raw = {"type": "NACK", "entry_id": "1708900000001-0", "reason": "busy"}
        frame = parse_agent_frame(raw)
        assert isinstance(frame, WSNackFrame)
        assert frame.reason == "busy"

    def test_parse_agent_frame_pong(self):
        raw = {"type": "PONG", "ping_id": "12345"}
        frame = parse_agent_frame(raw)
        assert isinstance(frame, WSPongFrame)

    def test_parse_agent_frame_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown agent frame type"):
            parse_agent_frame({"type": "NOT_A_FRAME"})

    def test_message_frame_json_roundtrip(self):
        """Frame must survive JSON serialisation (used over WS text channel)."""
        env = self._make_envelope()
        frame = WSMessageFrame(
            entry_id="1708900000000-0",
            envelope=env,
            stream="stream:exec_ceo",
        )
        raw = frame.model_dump_json()
        restored = WSMessageFrame.model_validate_json(raw)
        assert restored.entry_id == frame.entry_id
        assert restored.envelope.message_id == env.message_id


# ===========================================================================
# Additional coverage — audit gap-fills
# ===========================================================================


class TestOversizedPayloadWithBlobRef:
    """Oversized payload MUST be accepted when a blob_ref is provided."""

    def test_oversized_payload_accepted_with_blob_ref(self):
        big = {"data": "a" * (MAX_PAYLOAD_BYTES + 1)}
        ref = BlobRef(
            bucket="mas-agents",
            key="proj-001/documents/big.json",
            sha256="abc123" * 10,
            size_bytes=MAX_PAYLOAD_BYTES + 100,
        )
        env = _basic_envelope(payload=big, blob_ref=ref)
        assert env.blob_ref is not None
        assert env.payload == big

    def test_oversized_payload_without_blob_ref_still_rejected(self):
        big = {"data": "a" * (MAX_PAYLOAD_BYTES + 1)}
        with pytest.raises(ValueError, match="MAX_PAYLOAD_BYTES"):
            _basic_envelope(payload=big)


class TestHumanDecisionConstruction:
    """HumanDecision requires 'decision' as a mandatory field."""

    def test_approve_construction(self):
        hd = HumanDecision(
            project_id="p1",
            gate="FEASIBILITY",
            approved=True,
            decision="APPROVE",
        )
        assert hd.decision == "APPROVE"
        assert hd.approved is True
        assert hd.decided_by == "human"

    def test_reject_construction(self):
        hd = HumanDecision(
            project_id="p1",
            gate="CDR_APPROVAL",
            approved=False,
            decision="REJECT",
            comment="Budget exceeded",
        )
        assert hd.decision == "REJECT"
        assert hd.comment == "Budget exceeded"

    def test_edit_construction_with_instructions(self):
        hd = HumanDecision(
            project_id="p1",
            gate="CDR_APPROVAL",
            approved=False,
            decision="EDIT",
            edit_instructions="Reduce scope of module B.",
        )
        assert hd.decision == "EDIT"
        assert hd.edit_instructions is not None

    def test_missing_decision_raises(self):
        with pytest.raises(Exception):
            HumanDecision(
                project_id="p1",
                gate="FEASIBILITY",
                approved=True,
                # decision is missing — required field
            )


class TestReviewResponseConstruction:
    def test_basic_construction(self):
        rr = ReviewResponse(
            reviewer_id="cfo_agent",
            reviewer_role="C_SUITE",
            reviewer_team="office_cfo",
            verdict=ReviewVerdict.APPROVED,
        )
        assert rr.reviewer_id == "cfo_agent"
        assert rr.veto is False
        assert rr.comments == []

    def test_with_veto_and_comments(self):
        comment = ReviewComment(
            reviewer_id="cso_agent",
            reviewer_team="office_cso",
            severity=ReviewSeverity.BLOCKER,
            body="Critical security flaw in auth module.",
            category="security",
            suggested_change="Implement OAuth2 instead of basic auth.",
        )
        rr = ReviewResponse(
            reviewer_id="cso_agent",
            reviewer_role="C_SUITE",
            reviewer_team="office_cso",
            verdict=ReviewVerdict.REJECTED,
            comments=[comment],
            veto=True,
        )
        assert rr.veto is True
        assert len(rr.comments) == 1
        assert rr.comments[0].severity == ReviewSeverity.BLOCKER


class TestCorrelationIdDefault:
    """correlation_id should default to message_id when not explicitly set."""

    def test_correlation_id_defaults_to_message_id(self):
        env = _basic_envelope()
        assert env.correlation_id == env.message_id

    def test_explicit_correlation_id_preserved(self):
        cid = uuid4()
        env = _basic_envelope(correlation_id=cid)
        assert env.correlation_id == cid
        assert env.correlation_id != env.message_id


class TestSprintStatusEnum:
    """Sprint.status should use the SprintStatus enum."""

    def test_sprint_uses_sprint_status_enum(self):
        s = Sprint(project_id="p1", sprint_number=1, name="Sprint 1")
        assert s.status == SprintStatus.PLANNED

    def test_sprint_status_active(self):
        s = Sprint(project_id="p1", sprint_number=2, name="Sprint 2", status=SprintStatus.ACTIVE)
        assert s.status == SprintStatus.ACTIVE


class TestKPIMetricTypeEnum:
    """KPISnapshot.metric_type should use the KPIMetricType enum."""

    def test_kpi_snapshot_infra_lead_time(self):
        snap = KPISnapshot(project_id="p1", metric_type=KPIMetricType.INFRA_LEAD_TIME, value=120.0)
        assert snap.metric_type == KPIMetricType.INFRA_LEAD_TIME

    def test_kpi_snapshot_all_metric_types_accepted(self):
        for mt in KPIMetricType:
            snap = KPISnapshot(project_id="p1", metric_type=mt, value=1.0)
            assert snap.metric_type == mt
