"""Tests for Phase 8 — Agent Types.

Test classes
------------
TestWorkerAgent         — task execution, tool calls, fan-out budget check
TestAdminAgent          — task delegation, result aggregation, shutdown cascade
TestSubAgent            — subtask execution, direct invocation
TestExecutiveAgent      — document lifecycle, review fan-out/fan-in, CSO veto,
                          revision loops, department tasking
TestCSuiteAgent         — review handling, CSO veto, CTO sprint planning,
                          specialization routing
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from mas_core.agent_runtime.admin import AdminAgent
from mas_core.agent_runtime.base import AgentBase
from mas_core.agent_runtime.budget import BudgetTracker
from mas_core.agent_runtime.config import AgentConfig
from mas_core.agent_runtime.csuite import CSuiteAgent
from mas_core.agent_runtime.executive import ExecutiveAgent
from mas_core.agent_runtime.sub_agent import SubAgent
from mas_core.agent_runtime.worker import WorkerAgent
from mas_core.llm_gateway.models import (
    ChatMessage,
    ChatResponse,
    UsageStats,
)
from mas_core.protocols.enums import (
    AgentRole,
    MessageType,
    ReviewSeverity,
)
from mas_core.protocols.envelope import MessageEnvelope

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(**overrides: Any) -> AgentConfig:
    defaults = dict(
        agent_id="test_agent",
        team_id="dept_production",
        agent_role=AgentRole.WORKER,
        agent_secret="secret123",
        router_url="http://fake-router:8000",
    )
    defaults.update(overrides)
    return AgentConfig.model_construct(**defaults)


def _make_envelope(**overrides: Any) -> MessageEnvelope:
    defaults = dict(
        msg_type=MessageType.ADMIN_TASK,
        sender_id="admin_pm",
        sender_role=AgentRole.ADMIN,
        sender_team="dept_production",
        recipient_team="dept_production",
        project_id="proj-001",
        payload={"task": "build feature X", "context": "some context"},
    )
    defaults.update(overrides)
    return MessageEnvelope(**defaults)


class _FakeLLMClient:
    """Deterministic LLM stub that returns a canned assistant message."""

    def __init__(self, response_text: str = "done"):
        self._response_text = response_text
        self.started = False

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.started = False

    async def chat_completion(self, messages, **kwargs) -> ChatResponse:
        return ChatResponse(
            message=ChatMessage(role="assistant", content=self._response_text),
            usage=UsageStats(prompt_tokens=10, completion_tokens=5, estimated_cost_usd=0.001),
            model="test-model",
            finish_reason="stop",
        )


class _FakeToolClient:
    """Stub ToolServiceClient."""

    def __init__(self, result: Any = "tool_result"):
        self._result = result
        self.calls: list[dict] = []

    async def execute(self, *, tool_name: str, caller_id: str, caller_role: Any, kwargs: dict, **kw):
        self.calls.append({"tool_name": tool_name, "kwargs": kwargs})
        return MagicMock(success=True, result=self._result, data=None)


def _patch_router(agent: AgentBase) -> AsyncMock:
    """Replace the router with a mock that captures publish calls."""
    mock_router = AsyncMock()
    mock_router.publish = AsyncMock(return_value="entry-1")
    mock_router.broadcast = AsyncMock(return_value={"ok": True})
    mock_router.start = AsyncMock()
    mock_router.stop = AsyncMock()
    agent._router = mock_router
    return mock_router


# =====================================================================
# WorkerAgent Tests
# =====================================================================


class TestWorkerAgent:
    """WorkerAgent: task execution, tool calls, fan-out."""

    @pytest.mark.asyncio
    async def test_handle_admin_task(self):
        """Worker receives ADMIN_TASK, runs think(), publishes ADMIN_REPLY."""
        config = _make_config(agent_id="worker-1", agent_role=AgentRole.WORKER)
        llm = _FakeLLMClient("feature X built successfully")
        agent = WorkerAgent(config, llm_client=llm)
        router = _patch_router(agent)

        env = _make_envelope(msg_type=MessageType.ADMIN_TASK)
        agent._current_envelope = env
        agent._budget = BudgetTracker()

        await agent.handle_message(env)

        # Should have published exactly one ADMIN_REPLY
        assert router.publish.call_count == 1
        reply_env = router.publish.call_args[0][0]
        assert reply_env.msg_type == MessageType.ADMIN_REPLY
        assert reply_env.payload["result"] == "feature X built successfully"

    @pytest.mark.asyncio
    async def test_handle_issue_assign(self):
        """Worker receives ISSUE_ASSIGN, replies with ISSUE_COMPLETE."""
        config = _make_config(agent_id="worker-2", agent_role=AgentRole.WORKER)
        llm = _FakeLLMClient("issue fixed")
        agent = WorkerAgent(config, llm_client=llm)
        router = _patch_router(agent)

        env = _make_envelope(msg_type=MessageType.ISSUE_ASSIGN)
        agent._current_envelope = env
        agent._budget = BudgetTracker()

        await agent.handle_message(env)

        reply_env = router.publish.call_args[0][0]
        assert reply_env.msg_type == MessageType.ISSUE_COMPLETE

    @pytest.mark.asyncio
    async def test_execute_tool_via_client(self):
        """Worker delegates tool execution to ToolServiceClient."""
        config = _make_config(agent_id="worker-3")
        tool_client = _FakeToolClient(result="tool output")
        agent = WorkerAgent(config, tool_client=tool_client)

        env = _make_envelope()
        agent._current_envelope = env

        result = await agent.execute_tool("my_tool", {"arg": "val"})
        assert result == "tool output"
        assert tool_client.calls[0]["tool_name"] == "my_tool"

    @pytest.mark.asyncio
    async def test_spawn_subtask_within_budget(self):
        """Worker can spawn subtasks when budget allows."""
        config = _make_config(agent_id="worker-4")
        agent = WorkerAgent(config)
        router = _patch_router(agent)

        env = _make_envelope()
        agent._current_envelope = env
        agent._budget = BudgetTracker(max_subtasks=2)

        entry_id = await agent.spawn_subtask(task="subtask-1")
        assert entry_id == "entry-1"
        assert router.publish.call_count == 1

    @pytest.mark.asyncio
    async def test_spawn_subtask_budget_exhausted(self):
        """Worker returns None when subtask budget is exhausted."""
        config = _make_config(agent_id="worker-5")
        agent = WorkerAgent(config)
        _patch_router(agent)

        env = _make_envelope()
        agent._current_envelope = env
        agent._budget = BudgetTracker(max_subtasks=0)

        entry_id = await agent.spawn_subtask(task="subtask-denied")
        assert entry_id is None

    @pytest.mark.asyncio
    async def test_ignores_unhandled_message_type(self):
        """Worker ignores message types it doesn't handle."""
        config = _make_config(agent_id="worker-6")
        agent = WorkerAgent(config)
        _patch_router(agent)

        env = _make_envelope(msg_type=MessageType.BROADCAST)
        agent._current_envelope = env
        agent._budget = BudgetTracker()

        # Should not raise
        await agent.handle_message(env)


# =====================================================================
# AdminAgent Tests
# =====================================================================


class TestAdminAgent:
    """AdminAgent: delegation, aggregation, shutdown."""

    @pytest.mark.asyncio
    async def test_delegate_subtasks(self):
        """Admin delegates pre-decomposed subtasks to workers."""
        config = _make_config(agent_id="admin-pm", agent_role=AgentRole.ADMIN)
        llm = _FakeLLMClient("plan done")
        agent = AdminAgent(config, llm_client=llm)
        router = _patch_router(agent)

        env = _make_envelope(
            msg_type=MessageType.ADMIN_TASK,
            payload={
                "subtasks": [
                    {"task": "sub 1", "context": "ctx 1"},
                    {"task": "sub 2", "context": "ctx 2"},
                ],
            },
        )
        agent._current_envelope = env
        agent._budget = BudgetTracker()

        await agent.handle_message(env)

        # Two subtasks delegated
        assert router.publish.call_count == 2

    @pytest.mark.asyncio
    async def test_aggregate_replies(self):
        """Admin aggregates worker replies and sends consolidated reply."""
        config = _make_config(agent_id="admin-pm", agent_role=AgentRole.ADMIN)
        llm = _FakeLLMClient("plan done")
        agent = AdminAgent(config, llm_client=llm)
        router = _patch_router(agent)

        # First, delegate subtasks
        parent_env = _make_envelope(
            msg_type=MessageType.ADMIN_TASK,
            payload={
                "subtasks": [
                    {"task": "sub 1"},
                    {"task": "sub 2"},
                ],
            },
        )
        agent._current_envelope = parent_env
        agent._budget = BudgetTracker()
        await agent._handle_admin_task(parent_env)

        # Now simulate two worker replies
        corr_id = str(parent_env.correlation_id)
        router.publish.reset_mock()

        reply1 = _make_envelope(
            msg_type=MessageType.ADMIN_REPLY,
            sender_id="worker-1",
            correlation_id=parent_env.correlation_id,
            payload={"result": "result 1", "task": "sub 1"},
        )
        agent._current_envelope = reply1
        await agent._handle_admin_reply(reply1)

        reply2 = _make_envelope(
            msg_type=MessageType.ADMIN_REPLY,
            sender_id="worker-2",
            correlation_id=parent_env.correlation_id,
            payload={"result": "result 2", "task": "sub 2"},
        )
        agent._current_envelope = reply2
        await agent._handle_admin_reply(reply2)

        # Should have published aggregated reply
        assert router.publish.call_count >= 1
        last_call = router.publish.call_args[0][0]
        assert last_call.msg_type == MessageType.ADMIN_REPLY
        assert last_call.payload["result_count"] == 2

    @pytest.mark.asyncio
    async def test_shutdown_cascade(self):
        """Admin forwards shutdown to team and ACKs upstream."""
        config = _make_config(agent_id="admin-pm", agent_role=AgentRole.ADMIN)
        agent = AdminAgent(config)
        router = _patch_router(agent)

        env = _make_envelope(msg_type=MessageType.SHUTDOWN, sender_id="coo")
        agent._current_envelope = env
        agent._budget = BudgetTracker()

        await agent.handle_message(env)

        # Should publish SHUTDOWN to team + SHUTDOWN_ACK upstream
        assert router.publish.call_count == 2
        msg_types = [c[0][0].msg_type for c in router.publish.call_args_list]
        assert MessageType.SHUTDOWN in msg_types
        assert MessageType.SHUTDOWN_ACK in msg_types
        assert agent._shutting_down.is_set()

    @pytest.mark.asyncio
    async def test_issue_delegation(self):
        """Admin delegates ISSUE_ASSIGN to workers."""
        config = _make_config(agent_id="admin-pm", agent_role=AgentRole.ADMIN)
        agent = AdminAgent(config)
        router = _patch_router(agent)

        env = _make_envelope(
            msg_type=MessageType.ISSUE_ASSIGN,
            payload={"issue_type": "feature", "title": "test issue"},
        )
        agent._current_envelope = env
        agent._budget = BudgetTracker()

        await agent.handle_message(env)
        assert router.publish.call_count == 1
        published = router.publish.call_args[0][0]
        assert published.msg_type == MessageType.ISSUE_ASSIGN

    @pytest.mark.asyncio
    async def test_issue_complete_forwarded(self):
        """Admin forwards ISSUE_COMPLETE upstream."""
        config = _make_config(agent_id="admin-pm", agent_role=AgentRole.ADMIN)
        agent = AdminAgent(config)
        router = _patch_router(agent)

        env = _make_envelope(
            msg_type=MessageType.ISSUE_COMPLETE,
            payload={"result": "done"},
        )
        agent._current_envelope = env
        agent._budget = BudgetTracker()

        await agent.handle_message(env)
        assert router.publish.call_count == 1
        published = router.publish.call_args[0][0]
        assert published.msg_type == MessageType.ISSUE_COMPLETE

    @pytest.mark.asyncio
    async def test_directive_broadcast(self):
        """Admin re-broadcasts directives to team."""
        config = _make_config(agent_id="admin-pm", agent_role=AgentRole.ADMIN)
        agent = AdminAgent(config)
        router = _patch_router(agent)

        env = _make_envelope(
            msg_type=MessageType.DIRECTIVE,
            payload={"action": "RESUME"},
        )
        agent._current_envelope = env
        agent._budget = BudgetTracker()

        await agent.handle_message(env)
        assert router.publish.call_count == 1
        published = router.publish.call_args[0][0]
        assert published.msg_type == MessageType.DIRECTIVE


# =====================================================================
# SubAgent Tests
# =====================================================================


class TestSubAgent:
    """SubAgent: subtask execution."""

    @pytest.mark.asyncio
    async def test_handle_task(self):
        """SubAgent processes ADMIN_TASK and publishes ADMIN_REPLY."""
        config = _make_config(
            agent_id="sub-1", agent_role=AgentRole.SUB_AGENT
        )
        llm = _FakeLLMClient("subtask done")
        agent = SubAgent(config, llm_client=llm)
        router = _patch_router(agent)

        env = _make_envelope(
            msg_type=MessageType.ADMIN_TASK,
            payload={"task": "analyze data", "context": "quarterly report"},
        )
        agent._current_envelope = env
        agent._budget = BudgetTracker()

        await agent.handle_message(env)

        assert router.publish.call_count == 1
        reply = router.publish.call_args[0][0]
        assert reply.msg_type == MessageType.ADMIN_REPLY
        assert reply.payload["sub_agent"] is True
        assert reply.payload["result"] == "subtask done"

    @pytest.mark.asyncio
    async def test_direct_execute(self):
        """SubAgent.execute() returns result string directly."""
        config = _make_config(
            agent_id="sub-2", agent_role=AgentRole.SUB_AGENT
        )
        llm = _FakeLLMClient("direct result")
        agent = SubAgent(config, llm_client=llm)
        _patch_router(agent)

        agent._budget = BudgetTracker()
        result = await agent.execute("compute average", "numbers: 1,2,3")
        assert result == "direct result"

    @pytest.mark.asyncio
    async def test_ignores_non_task_messages(self):
        """SubAgent ignores non-task message types."""
        config = _make_config(
            agent_id="sub-3", agent_role=AgentRole.SUB_AGENT
        )
        agent = SubAgent(config)
        _patch_router(agent)

        env = _make_envelope(msg_type=MessageType.BROADCAST)
        agent._current_envelope = env
        agent._budget = BudgetTracker()

        await agent.handle_message(env)

    @pytest.mark.asyncio
    async def test_tool_execution(self):
        """SubAgent delegates tool execution to ToolServiceClient."""
        config = _make_config(agent_id="sub-4", agent_role=AgentRole.SUB_AGENT)
        tool_client = _FakeToolClient(result="sub tool result")
        agent = SubAgent(config, tool_client=tool_client)

        env = _make_envelope()
        agent._current_envelope = env

        result = await agent.execute_tool("sub_tool", {"key": "val"})
        assert result == "sub tool result"


# =====================================================================
# ExecutiveAgent Tests
# =====================================================================


class TestExecutiveAgent:
    """ExecutiveAgent: document lifecycle, review, CSO veto, revision."""

    @pytest.mark.asyncio
    async def test_document_submit_triggers_review_fanout(self):
        """Executive fans out REVIEW_REQUEST on DOCUMENT_SUBMIT."""
        config = _make_config(
            agent_id="coo-1", team_id="exec_coo", agent_role=AgentRole.EXECUTIVE
        )
        llm = _FakeLLMClient("ok")
        events: list = []
        agent = ExecutiveAgent(
            config,
            llm_client=llm,
            reviewer_teams=["office_cfo", "office_cso", "office_cto"],
            event_emitter=lambda evt, **kw: events.append((evt, kw)),
        )
        router = _patch_router(agent)

        env = _make_envelope(
            msg_type=MessageType.DOCUMENT_SUBMIT,
            sender_id="dept_production_pm",
            sender_team="dept_production",
            payload={
                "document_id": str(uuid4()),
                "doc_type": "PDR",
                "task": "build system X",
            },
        )
        agent._current_envelope = env
        agent._budget = BudgetTracker()

        await agent.handle_message(env)

        # 3 REVIEW_REQUEST messages (one per reviewer team)
        assert router.publish.call_count == 3
        for call in router.publish.call_args_list:
            published = call[0][0]
            assert published.msg_type == MessageType.REVIEW_REQUEST

        # Event emitted
        assert any(e[0] == "document_submitted" for e in events)

    @pytest.mark.asyncio
    async def test_review_approved_emits_event(self):
        """All reviewers approve → review_approved event."""
        config = _make_config(
            agent_id="coo-2", team_id="exec_coo", agent_role=AgentRole.EXECUTIVE
        )
        events: list = []
        agent = ExecutiveAgent(
            config,
            reviewer_teams=["office_cfo", "office_cso"],
            event_emitter=lambda evt, **kw: events.append((evt, kw)),
        )
        router = _patch_router(agent)

        # Start a review session manually
        parent_env = _make_envelope(
            msg_type=MessageType.DOCUMENT_SUBMIT,
            payload={"document_id": str(uuid4()), "doc_type": "PDR"},
        )
        session_id = str(uuid4())
        await agent._start_review_fanout(
            session_id=session_id,
            document_id=str(uuid4()),
            doc_type="PDR",
            parent_envelope=parent_env,
        )
        router.publish.reset_mock()

        # Simulate review responses
        for reviewer, team in [("cfo-1", "office_cfo"), ("cso-1", "office_cso")]:
            resp_env = _make_envelope(
                msg_type=MessageType.REVIEW_RESPONSE,
                sender_id=reviewer,
                sender_team=team,
                payload={
                    "session_id": session_id,
                    "verdict": "APPROVED",
                    "comments": [],
                },
            )
            agent._current_envelope = resp_env
            agent._budget = BudgetTracker()
            await agent._handle_review_response(resp_env)

        assert any(e[0] == "review_approved" for e in events)

    @pytest.mark.asyncio
    async def test_cso_veto_emits_event(self):
        """CSO veto triggers cso_veto event."""
        config = _make_config(
            agent_id="coo-3", team_id="exec_coo", agent_role=AgentRole.EXECUTIVE
        )
        events: list = []
        agent = ExecutiveAgent(
            config,
            reviewer_teams=["office_cfo", "office_cso"],
            event_emitter=lambda evt, **kw: events.append((evt, kw)),
        )
        router = _patch_router(agent)

        parent_env = _make_envelope(
            msg_type=MessageType.DOCUMENT_SUBMIT,
            payload={"document_id": str(uuid4()), "doc_type": "CDR"},
        )
        session_id = str(uuid4())
        await agent._start_review_fanout(
            session_id=session_id,
            document_id=str(uuid4()),
            doc_type="CDR",
            parent_envelope=parent_env,
        )
        router.publish.reset_mock()

        # CSO submits with veto
        veto_env = _make_envelope(
            msg_type=MessageType.REVIEW_RESPONSE,
            sender_id="cso-1",
            sender_team="office_cso",
            payload={
                "session_id": session_id,
                "verdict": "REJECTED",
                "veto": True,
                "comments": [
                    {
                        "severity": "BLOCKER",
                        "body": "Critical security vulnerability",
                        "veto": True,
                    }
                ],
            },
        )
        agent._current_envelope = veto_env
        agent._budget = BudgetTracker()
        await agent._handle_review_response(veto_env)

        assert any(e[0] == "cso_veto" for e in events)
        # Session should be cleaned up
        assert session_id not in agent._review_sessions

    @pytest.mark.asyncio
    async def test_needs_revision_triggers_revision_request(self):
        """NEEDS_REVISION verdict sends DOCUMENT_REVISION back."""
        config = _make_config(
            agent_id="coo-4", team_id="exec_coo", agent_role=AgentRole.EXECUTIVE
        )
        events: list = []
        agent = ExecutiveAgent(
            config,
            reviewer_teams=["office_cfo"],
            event_emitter=lambda evt, **kw: events.append((evt, kw)),
        )
        router = _patch_router(agent)

        parent_env = _make_envelope(
            msg_type=MessageType.DOCUMENT_SUBMIT,
            sender_team="dept_production",
            payload={"document_id": str(uuid4()), "doc_type": "PDR"},
        )
        session_id = str(uuid4())
        await agent._start_review_fanout(
            session_id=session_id,
            document_id=str(uuid4()),
            doc_type="PDR",
            parent_envelope=parent_env,
        )
        router.publish.reset_mock()

        resp_env = _make_envelope(
            msg_type=MessageType.REVIEW_RESPONSE,
            sender_id="cfo-1",
            sender_team="office_cfo",
            payload={
                "session_id": session_id,
                "verdict": "NEEDS_REVISION",
                "comments": [
                    {"severity": "MAJOR", "body": "Budget section incomplete"},
                ],
            },
        )
        agent._current_envelope = resp_env
        agent._budget = BudgetTracker()
        await agent._handle_review_response(resp_env)

        # Should publish DOCUMENT_REVISION
        assert router.publish.call_count >= 1
        revision_msgs = [
            c[0][0] for c in router.publish.call_args_list
            if c[0][0].msg_type == MessageType.DOCUMENT_REVISION
        ]
        assert len(revision_msgs) == 1
        assert revision_msgs[0].payload["revision_number"] == 1

    @pytest.mark.asyncio
    async def test_max_revisions_exceeded(self):
        """After max revisions, review is rejected."""
        config = _make_config(
            agent_id="coo-5", team_id="exec_coo", agent_role=AgentRole.EXECUTIVE
        )
        events: list = []
        agent = ExecutiveAgent(
            config,
            reviewer_teams=["office_cfo"],
            max_revisions=1,
            event_emitter=lambda evt, **kw: events.append((evt, kw)),
        )
        router = _patch_router(agent)

        doc_id = str(uuid4())
        parent_env = _make_envelope(
            msg_type=MessageType.DOCUMENT_SUBMIT,
            sender_team="dept_production",
            payload={"document_id": doc_id, "doc_type": "PDR"},
        )

        # First revision
        from mas_core.protocols.domain import ReviewComment as RC
        from mas_core.protocols.domain import ReviewSummary
        summary = ReviewSummary(
            project_id="proj-001",
            document_id=uuid4(),
            doc_type="PDR",
        )
        summary.comments.append(RC(
            reviewer_id="cfo-1", reviewer_team="office_cfo",
            severity=ReviewSeverity.MAJOR, body="issue",
        ))
        await agent._request_revision(summary, parent_env)
        router.publish.reset_mock()

        # Second revision attempt (exceeds max_revisions=1)
        await agent._request_revision(summary, parent_env)
        assert any(e[0] == "review_rejected" for e in events)

    @pytest.mark.asyncio
    async def test_review_circuit_breaker(self):
        """≥2 timeouts triggers circuit_open event."""
        config = _make_config(
            agent_id="coo-6", team_id="exec_coo", agent_role=AgentRole.EXECUTIVE
        )
        events: list = []
        agent = ExecutiveAgent(
            config,
            reviewer_teams=["office_cfo", "office_cso", "office_cto"],
            event_emitter=lambda evt, **kw: events.append((evt, kw)),
        )
        _patch_router(agent)

        parent_env = _make_envelope(
            msg_type=MessageType.DOCUMENT_SUBMIT,
            payload={"document_id": str(uuid4()), "doc_type": "PDR"},
        )
        session_id = str(uuid4())
        await agent._start_review_fanout(
            session_id=session_id,
            document_id=str(uuid4()),
            doc_type="PDR",
            parent_envelope=parent_env,
        )

        # Two timeouts → circuit open
        await agent.record_review_timeout(session_id, "cfo-1")
        await agent.record_review_timeout(session_id, "cso-1")

        assert any(e[0] == "review_circuit_open" for e in events)
        # Session cleaned up
        assert session_id not in agent._review_sessions

    @pytest.mark.asyncio
    async def test_task_department(self):
        """Executive can delegate tasks to department PMs."""
        config = _make_config(
            agent_id="coo-7", team_id="exec_coo", agent_role=AgentRole.EXECUTIVE
        )
        agent = ExecutiveAgent(config)
        router = _patch_router(agent)

        env = _make_envelope()
        agent._current_envelope = env
        agent._budget = BudgetTracker()

        entry_id = await agent.task_department(
            team_id="dept_production",
            task="create PDR",
            context="for project X",
        )
        assert entry_id == "entry-1"
        published = router.publish.call_args[0][0]
        assert published.msg_type == MessageType.ADMIN_TASK
        assert published.recipient_team == "dept_production"

    @pytest.mark.asyncio
    async def test_approval_response_emits_event(self):
        """Human approval response emits human_decision event."""
        config = _make_config(
            agent_id="coo-8", team_id="exec_coo", agent_role=AgentRole.EXECUTIVE
        )
        events: list = []
        agent = ExecutiveAgent(
            config,
            event_emitter=lambda evt, **kw: events.append((evt, kw)),
        )
        _patch_router(agent)

        env = _make_envelope(
            msg_type=MessageType.APPROVAL_RESPONSE,
            payload={"decision": "APPROVE", "comment": "looks good"},
        )
        agent._current_envelope = env
        agent._budget = BudgetTracker()

        await agent.handle_message(env)
        assert any(e[0] == "human_decision" for e in events)

    @pytest.mark.asyncio
    async def test_infra_ready_emits_event(self):
        """INFRA_READY triggers infra_ready event."""
        config = _make_config(
            agent_id="coo-9", team_id="exec_coo", agent_role=AgentRole.EXECUTIVE
        )
        events: list = []
        agent = ExecutiveAgent(
            config,
            event_emitter=lambda evt, **kw: events.append((evt, kw)),
        )
        _patch_router(agent)

        env = _make_envelope(
            msg_type=MessageType.INFRA_READY,
            payload={"sprint_id": "sprint-1"},
        )
        agent._current_envelope = env
        agent._budget = BudgetTracker()

        await agent.handle_message(env)
        assert any(e[0] == "infra_ready" for e in events)


# =====================================================================
# CSuiteAgent Tests
# =====================================================================


class TestCSuiteAgent:
    """CSuiteAgent: review, veto, specialization."""

    @pytest.mark.asyncio
    async def test_review_request_produces_response(self):
        """CSuite agent reviews a document and publishes REVIEW_RESPONSE."""
        config = _make_config(
            agent_id="cfo-1", team_id="office_cfo", agent_role=AgentRole.C_SUITE
        )
        llm = _FakeLLMClient("APPROVED. Budget analysis looks correct.")
        agent = CSuiteAgent(config, specialization="CFO", llm_client=llm)
        router = _patch_router(agent)

        env = _make_envelope(
            msg_type=MessageType.REVIEW_REQUEST,
            sender_id="coo-1",
            sender_team="exec_coo",
            payload={
                "session_id": str(uuid4()),
                "document_id": str(uuid4()),
                "doc_type": "PDR",
                "document_payload": {"task": "build system X"},
            },
        )
        agent._current_envelope = env
        agent._budget = BudgetTracker()

        await agent.handle_message(env)

        assert router.publish.call_count == 1
        reply = router.publish.call_args[0][0]
        assert reply.msg_type == MessageType.REVIEW_RESPONSE
        assert reply.payload["verdict"] == "APPROVED"

    @pytest.mark.asyncio
    async def test_cso_veto_detection(self):
        """CSO agent detects veto triggers in review text."""
        config = _make_config(
            agent_id="cso-1", team_id="office_cso", agent_role=AgentRole.C_SUITE
        )
        llm = _FakeLLMClient("VETO. Critical SQL injection vulnerability found.")
        agent = CSuiteAgent(config, specialization="CSO", llm_client=llm)
        router = _patch_router(agent)

        env = _make_envelope(
            msg_type=MessageType.REVIEW_REQUEST,
            sender_id="coo-1",
            sender_team="exec_coo",
            payload={
                "session_id": str(uuid4()),
                "document_id": str(uuid4()),
                "doc_type": "CDR",
                "document_payload": {"task": "deploy database"},
            },
        )
        agent._current_envelope = env
        agent._budget = BudgetTracker()

        await agent.handle_message(env)

        reply = router.publish.call_args[0][0]
        assert reply.payload["veto"] is True
        assert reply.payload["verdict"] == "REJECTED"

    @pytest.mark.asyncio
    async def test_query_response(self):
        """CSuite responds to QUERY with domain analysis."""
        config = _make_config(
            agent_id="cio-1", team_id="office_cio", agent_role=AgentRole.C_SUITE
        )
        llm = _FakeLLMClient("Python 3.11 is recommended for this stack.")
        agent = CSuiteAgent(config, specialization="CIO", llm_client=llm)
        router = _patch_router(agent)

        env = _make_envelope(
            msg_type=MessageType.QUERY,
            payload={"query": "What Python version should we use?", "context": "new project"},
        )
        agent._current_envelope = env
        agent._budget = BudgetTracker()

        await agent.handle_message(env)

        reply = router.publish.call_args[0][0]
        assert reply.msg_type == MessageType.RESPONSE
        assert "Python" in reply.payload["response"]

    @pytest.mark.asyncio
    async def test_cto_sprint_planning(self):
        """CTO handles SPRINT_PLAN with decomposition."""
        config = _make_config(
            agent_id="cto-1", team_id="office_cto", agent_role=AgentRole.C_SUITE
        )
        llm = _FakeLLMClient("Sprint plan: 3 features, 2 tests, 1 infra issue.")
        agent = CSuiteAgent(config, specialization="CTO", llm_client=llm)
        router = _patch_router(agent)

        env = _make_envelope(
            msg_type=MessageType.SPRINT_PLAN,
            payload={
                "sprint": {"name": "Sprint 1"},
                "requirements": ["Build API", "Setup CI/CD"],
            },
        )
        agent._current_envelope = env
        agent._budget = BudgetTracker()

        await agent.handle_message(env)

        assert router.publish.call_count == 1
        published = router.publish.call_args[0][0]
        assert published.msg_type == MessageType.SPRINT_PLAN

    @pytest.mark.asyncio
    async def test_non_cto_ignores_sprint_plan(self):
        """Non-CTO CSuite agent ignores SPRINT_PLAN."""
        config = _make_config(
            agent_id="cfo-2", team_id="office_cfo", agent_role=AgentRole.C_SUITE
        )
        agent = CSuiteAgent(config, specialization="CFO")
        router = _patch_router(agent)

        env = _make_envelope(
            msg_type=MessageType.SPRINT_PLAN,
            payload={"sprint": {"name": "Sprint 1"}, "requirements": []},
        )
        agent._current_envelope = env
        agent._budget = BudgetTracker()

        await agent.handle_message(env)
        # Should not publish anything
        assert router.publish.call_count == 0

    @pytest.mark.asyncio
    async def test_cto_sprint_report_kpi(self):
        """CTO processes sprint report and records KPI."""
        config = _make_config(
            agent_id="cto-2", team_id="office_cto", agent_role=AgentRole.C_SUITE
        )
        agent = CSuiteAgent(config, specialization="CTO")
        router = _patch_router(agent)

        env = _make_envelope(
            msg_type=MessageType.SPRINT_REPORT,
            payload={
                "sprint_number": 1,
                "completed_story_points": 8,
                "total_story_points": 10,
            },
        )
        agent._current_envelope = env
        agent._budget = BudgetTracker()

        await agent.handle_message(env)

        # KPI should be recorded
        assert len(agent.kpi_history) == 1
        assert agent.kpi_history[0].velocity == pytest.approx(0.8)
        assert router.publish.call_count == 1

    @pytest.mark.asyncio
    async def test_cto_infra_ready(self):
        """CTO acknowledges INFRA_READY."""
        config = _make_config(
            agent_id="cto-3", team_id="office_cto", agent_role=AgentRole.C_SUITE
        )
        agent = CSuiteAgent(config, specialization="CTO")
        router = _patch_router(agent)

        env = _make_envelope(
            msg_type=MessageType.INFRA_READY,
            payload={"sprint_id": "sprint-1"},
        )
        agent._current_envelope = env
        agent._budget = BudgetTracker()

        await agent.handle_message(env)


        assert router.publish.call_count == 1
        ack = router.publish.call_args[0][0]
        assert ack.msg_type == MessageType.ACK

    @pytest.mark.asyncio
    async def test_specialization_properties(self):
        """CSuiteAgent exposes specialization properties."""
        config = _make_config(agent_id="cso-2", agent_role=AgentRole.C_SUITE)
        cso = CSuiteAgent(config, specialization="CSO")
        assert cso.is_cso is True
        assert cso.is_cto is False
        assert cso.specialization == "CSO"

        cto = CSuiteAgent(config, specialization="CTO")
        assert cto.is_cto is True
        assert cto.is_cso is False

    @pytest.mark.asyncio
    async def test_issue_decomposition(self):
        """CTO decomposes requirements into typed Issues."""
        config = _make_config(
            agent_id="cto-4", team_id="office_cto", agent_role=AgentRole.C_SUITE
        )
        agent = CSuiteAgent(config, specialization="CTO")

        issues = await agent.decompose_issues(
            requirements=[
                {"title": "Build API", "type": "feature", "estimated_hours": 8},
                {"title": "Setup CI/CD", "type": "infra", "estimated_hours": 4},
                {"title": "Write tests", "type": "test", "story_points": 3},
            ],
            project_id="proj-001",
            sprint_id=str(uuid4()),
        )

        assert len(issues) == 3
        assert issues[0].issue_type.value == "feature"
        assert issues[1].issue_type.value == "infra"
        assert issues[2].issue_type.value == "test"
        assert issues[0].estimated_hours == 8.0


# =====================================================================
# Integration — __init__ exports
# =====================================================================


class TestPhase8Exports:
    """Verify all Phase 8 types are exported from agent_runtime."""

    def test_all_agent_types_importable(self):
        from mas_core.agent_runtime import (
            AdminAgent,
            CSuiteAgent,
            ExecutiveAgent,
            SubAgent,
            WorkerAgent,
        )
        assert WorkerAgent is not None
        assert AdminAgent is not None
        assert SubAgent is not None
        assert ExecutiveAgent is not None
        assert CSuiteAgent is not None

    def test_worker_is_agent_base(self):
        assert issubclass(WorkerAgent, AgentBase)

    def test_admin_is_agent_base(self):
        assert issubclass(AdminAgent, AgentBase)

    def test_sub_agent_is_agent_base(self):
        assert issubclass(SubAgent, AgentBase)

    def test_executive_extends_admin(self):
        assert issubclass(ExecutiveAgent, AdminAgent)

    def test_csuite_extends_admin(self):
        assert issubclass(CSuiteAgent, AdminAgent)
