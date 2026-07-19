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
    ToolCall,
    ToolCallFunction,
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
        self.calls: list[dict[str, Any]] = []

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.started = False

    async def chat_completion(self, messages, **kwargs) -> ChatResponse:
        self.calls.append({"messages": messages, "kwargs": kwargs})
        return ChatResponse(
            message=ChatMessage(role="assistant", content=self._response_text),
            usage=UsageStats(prompt_tokens=10, completion_tokens=5, estimated_cost_usd=0.001),
            model="test-model",
            finish_reason="stop",
        )

    async def chat_completion_with_fallback(self, messages, **kwargs) -> ChatResponse:
        return await self.chat_completion(messages, **kwargs)


class _FailingLLMClient(_FakeLLMClient):
    async def chat_completion(self, messages, **kwargs) -> ChatResponse:
        self.calls.append({"messages": messages, "kwargs": kwargs})
        raise RuntimeError("gateway unavailable")


class _FakeToolClient:
    """Stub ToolServiceClient."""

    def __init__(self, result: Any = "tool_result"):
        self._result = result
        self.calls: list[dict] = []

    async def execute(self, *, tool_name: str, caller_id: str, caller_role: Any, kwargs: dict, **kw):
        self.calls.append({"tool_name": tool_name, "kwargs": kwargs})
        return MagicMock(success=True, result=self._result, data=None)


class _FakeToolCallLLMClient(_FakeLLMClient):
    def __init__(self, tool_call: ToolCall):
        super().__init__("")
        self._tool_call = tool_call

    async def chat_completion(self, messages, **kwargs) -> ChatResponse:
        self.calls.append({"messages": messages, "kwargs": kwargs})
        return ChatResponse(
            message=ChatMessage(role="assistant", content=None, tool_calls=[self._tool_call]),
            tool_calls=[self._tool_call],
            usage=UsageStats(prompt_tokens=10, completion_tokens=5, estimated_cost_usd=0.001),
            model="test-model",
            finish_reason="tool_calls",
        )


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
    async def test_failed_llm_call_records_project_usage(self):
        project_id = "00000000-0000-4000-a000-000000000124"
        storage = MagicMock()
        storage.record_project_usage = AsyncMock(return_value={})
        agent = WorkerAgent(
            _make_config(),
            storage=storage,
            llm_client=_FailingLLMClient(),
        )
        agent._current_envelope = _make_envelope(project_id=project_id)
        agent._budget = BudgetTracker()

        with pytest.raises(RuntimeError, match="gateway unavailable"):
            await agent.think(messages=[{"role": "user", "content": "work"}])

        usage = storage.record_project_usage.await_args.kwargs
        assert str(usage["project_id"]) == project_id
        assert usage["event_type"] == "llm"
        assert usage["status"] == "error"
        assert usage["details"]["error_type"] == "RuntimeError"

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
            sender_id="coo",
            sender_role=AgentRole.EXECUTIVE,
            sender_team="exec_coo",
            recipient_team="dept_production",
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
        assert last_call.recipient_team == "exec_coo"
        assert last_call.parent_id == parent_env.message_id

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
    async def test_directive_delegates_as_admin_task(self):
        """Admin directives use the worker-task path and cannot loop on a team stream."""
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
        assert published.msg_type == MessageType.ADMIN_TASK
        assert published.payload["directive_forwarded"] is True

    @pytest.mark.asyncio
    async def test_devops_resume_directive_runs_infrastructure_adapter(self):
        config = _make_config(
            agent_id="devops-pm",
            team_id="dept_devops",
            agent_role=AgentRole.ADMIN,
        )
        agent = AdminAgent(config)
        agent.execute_tool = AsyncMock(
            side_effect=[
                {"available": True, "configured": True, "verified": True},
                {"next_state": "IN_PROGRESS"},
            ]
        )
        router = _patch_router(agent)
        env = _make_envelope(
            msg_type=MessageType.DIRECTIVE,
            sender_team="exec_ceo",
            recipient_team="dept_devops",
            project_id="project-infra",
            payload={"action": "RESUME", "state": "INFRA_PROVISIONING"},
        )
        agent._current_envelope = env
        agent._budget = BudgetTracker()

        await agent.handle_message(env)

        assert [call.args[0] for call in agent.execute_tool.await_args_list] == [
            "infra.provision",
            "infra.ready_signal",
        ]
        router.publish.assert_not_called()


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
    async def test_generic_task_is_executed_directly_and_replied_upstream(self):
        config = _make_config(
            agent_id="coo", team_id="exec_coo", agent_role=AgentRole.EXECUTIVE
        )
        agent = ExecutiveAgent(config, llm_client=_FakeLLMClient("executive result"))
        router = _patch_router(agent)
        env = _make_envelope(
            msg_type=MessageType.TASK,
            sender_id="ceo",
            sender_role=AgentRole.ORCHESTRATOR,
            sender_team="exec_ceo",
            recipient_team="exec_coo",
            payload={"task": "analyze operations", "context": "live audit"},
        )
        agent._current_envelope = env
        agent._budget = BudgetTracker()

        await agent.handle_message(env)

        assert router.publish.call_count == 1
        reply = router.publish.call_args.args[0]
        assert reply.msg_type == MessageType.ADMIN_REPLY
        assert reply.recipient_team == "exec_ceo"
        assert reply.payload["result"] == "executive result"
        assert agent._pending_delegations == {}

    @pytest.mark.asyncio
    async def test_execution_dispatches_open_issues_and_waits_for_completion(self):
        """Execution advances only after worker-owned issue completion is durable."""
        agent = ExecutiveAgent(
            _make_config(agent_id="coo-multi-sprint", agent_role=AgentRole.EXECUTIVE)
        )
        sprints = [
            {"id": "sprint-1", "sprint_number": 1, "status": "CLOSED"},
            {"id": "sprint-2", "sprint_number": 2, "status": "PLANNED"},
        ]
        issues: dict[str, list[dict[str, Any]]] = {
            "sprint-1": [{"id": "issue-1", "status": "DONE"}],
            "sprint-2": [],
        }
        calls: list[tuple[str, dict[str, Any]]] = []
        transitions: list[dict[str, Any]] = []

        async def execute_tool(name: str, kwargs: dict[str, Any]) -> Any:
            calls.append((name, kwargs))
            if name == "project.status":
                return {"state": "IN_PROGRESS"}
            if name == "sprint.list":
                return {"sprints": sprints}
            if name == "issue.list":
                return {"issues": issues[str(kwargs["sprint_id"])]}
            if name == "issue.create":
                issue = {"id": "issue-2", "status": "TODO", "issue_type": "TEST"}
                issues[str(kwargs["sprint_id"])].append(issue)
                return {"status": "completed", "result": issue}
            if name == "sprint.activate":
                next(item for item in sprints if item["id"] == kwargs["sprint_id"])["status"] = "IN_PROGRESS"
                return {"status": "completed"}
            if name == "department_task":
                assert kwargs["issue_id"] == "issue-2"
                assert kwargs["team"] == "dept_qa"
                return {"status": "published", "message_id": "work-1"}
            if name == "issue.update_status":
                issue = next(
                    issue
                    for sprint_issues in issues.values()
                    for issue in sprint_issues
                    if issue["id"] == kwargs["issue_id"]
                )
                issue["status"] = kwargs["status"]
                return {"status": "completed"}
            if name == "sprint.close":
                next(item for item in sprints if item["id"] == kwargs["sprint_id"])["status"] = "CLOSED"
                return {"status": "completed"}
            if name == "project.transition":
                transitions.append(kwargs)
                return {"status": "completed"}
            raise AssertionError(f"unexpected tool {name}")

        agent.execute_tool = AsyncMock(side_effect=execute_tool)
        envelope = _make_envelope(
            msg_type=MessageType.DIRECTIVE,
            project_id="project-multi-sprint",
            payload={"action": "START_EXECUTION", "state": "IN_PROGRESS"},
        )

        await agent._recover_directive_progress(envelope, "START_EXECUTION")

        assert [name for name, _ in calls].count("sprint.activate") == 1
        assert [kwargs["sprint_id"] for name, kwargs in calls if name == "sprint.activate"] == [
            "sprint-2"
        ]
        assert [name for name, _ in calls].count("department_task") == 1
        status_updates = [kwargs for name, kwargs in calls if name == "issue.update_status"]
        assert status_updates == [
            {
                "project_id": "project-multi-sprint",
                "issue_id": "issue-2",
                "status": "IN_PROGRESS",
            }
        ]
        assert not [kwargs for name, kwargs in calls if name == "sprint.close"]
        assert transitions == []
        assert issues["sprint-2"][0]["status"] == "IN_PROGRESS"

        # An empty reply is not completion evidence.
        rejected_reply = _make_envelope(
            msg_type=MessageType.ADMIN_REPLY,
            sender_id="qa-pm",
            sender_team="dept_qa",
            recipient_team="exec_coo",
            project_id="project-multi-sprint",
            payload={
                "action": "EXECUTE_ISSUE",
                "issue_id": "issue-2",
                "sprint_id": "sprint-2",
                "results": [{"sender": "tester", "result": ""}],
            },
        )
        await agent.handle_message(rejected_reply)
        assert issues["sprint-2"][0]["status"] == "IN_PROGRESS"

        # A non-empty worker output is validated before the issue is marked
        # done, then authoritative sprint reconciliation can advance.
        completed_reply = rejected_reply.model_copy(
            update={
                "message_id": uuid4(),
                "payload": {
                    **rejected_reply.payload,
                    "results": [{"sender": "tester", "result": "Verified test artifact"}],
                },
            }
        )
        await agent.handle_message(completed_reply)

        assert [name for name, _ in calls].count("department_task") == 1
        assert [
            kwargs["status"] for name, kwargs in calls if name == "issue.update_status"
        ] == ["IN_PROGRESS", "DONE"]
        assert [kwargs["sprint_id"] for name, kwargs in calls if name == "sprint.close"] == [
            "sprint-2"
        ]
        assert transitions and transitions[0]["event"] == "all_sprints_done"
        assert all(item["status"] == "CLOSED" for item in sprints)

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
    async def test_review_persists_unregistered_document_and_completion_time(self):
        """A document submission creates missing metadata and durable completion."""
        config = _make_config(
            agent_id="coo-persist", team_id="exec_coo", agent_role=AgentRole.EXECUTIVE
        )
        storage = MagicMock()
        storage.get_document = AsyncMock(return_value=None)
        storage.create_document = AsyncMock()
        storage.create_review_session = AsyncMock()
        storage.add_review_comment = AsyncMock()
        storage.update_review_session = AsyncMock()
        storage.update_document_status = AsyncMock()
        agent = ExecutiveAgent(
            config,
            reviewer_teams=["office_cfo"],
            review_storage=storage,
        )
        _patch_router(agent)
        project_id = uuid4()
        document_id = uuid4()
        parent = _make_envelope(
            msg_type=MessageType.DOCUMENT_SUBMIT,
            project_id=str(project_id),
            payload={"document_id": str(document_id), "doc_type": "PDR"},
        )
        session_id = str(uuid4())

        await agent._start_review_fanout(
            session_id=session_id,
            document_id=str(document_id),
            doc_type="PDR",
            parent_envelope=parent,
        )
        storage.create_document.assert_awaited_once_with(
            project_id=project_id,
            doc_type="PDR",
            created_by="coo-persist",
            document_id=document_id,
        )
        storage.create_review_session.assert_awaited_once()

        response = _make_envelope(
            msg_type=MessageType.REVIEW_RESPONSE,
            project_id=str(project_id),
            sender_id="cfo-persist",
            sender_team="office_cfo",
            payload={"session_id": session_id, "verdict": "APPROVED", "comments": []},
        )
        await agent._handle_review_response(response)

        update_kwargs = storage.update_review_session.await_args.kwargs
        assert update_kwargs["status"] == "COMPLETED"
        assert update_kwargs["completed_at"] is not None
        assert [call.kwargs["status"] for call in storage.update_document_status.await_args_list] == [
            "IN_REVIEW",
            "APPROVED",
        ]

    @pytest.mark.asyncio
    async def test_review_response_rehydrates_durable_session_after_restart(self):
        """A queued response restores the COO session instead of being discarded."""
        project_id = uuid4()
        document_id = uuid4()
        session_id = uuid4()
        storage = MagicMock()
        storage.get_review_session = AsyncMock(
            return_value={
                "id": session_id,
                "project_id": project_id,
                "document_id": document_id,
                "session_type": "PDR",
                "status": "IN_PROGRESS",
                "reviewer_ids": ["office_cfo"],
                "timeout_count": 0,
            }
        )
        storage.get_review_comments = AsyncMock(return_value=[])
        storage.add_review_comment = AsyncMock()
        storage.update_review_session = AsyncMock()
        storage.update_document_status = AsyncMock()
        agent = ExecutiveAgent(
            _make_config(agent_id="coo-restarted", agent_role=AgentRole.EXECUTIVE),
            reviewer_teams=["office_cfo"],
            review_storage=storage,
        )
        _patch_router(agent)
        transitions: list[dict[str, Any]] = []

        async def execute_tool(name: str, kwargs: dict[str, Any]) -> dict[str, Any]:
            if name == "project.transition":
                transitions.append(kwargs)
                return {"status": "completed"}
            raise AssertionError(f"unexpected tool {name}")

        agent.execute_tool = AsyncMock(side_effect=execute_tool)
        response = _make_envelope(
            msg_type=MessageType.REVIEW_RESPONSE,
            project_id=str(project_id),
            sender_id="cfo-1",
            sender_team="office_cfo",
            payload={
                "session_id": str(session_id),
                "verdict": "APPROVED",
                "comments": [],
            },
        )

        await agent._handle_review_response(response)

        storage.get_review_session.assert_awaited_once_with(session_id)
        storage.add_review_comment.assert_awaited_once()
        assert transitions[0]["event"] == "all_reviews_in"
        assert str(session_id) not in agent._review_sessions
        status_updates = storage.update_review_session.await_args_list
        assert any(call.kwargs.get("status") == "COMPLETED" for call in status_updates)

    @pytest.mark.asyncio
    async def test_veto_and_circuit_open_persist_completion_time(self):
        """Every terminal review outcome carries a durable completion time."""
        from mas_core.protocols.domain import ReviewResponse, ReviewSummary
        from mas_core.protocols.enums import ReviewVerdict

        for terminal_status in ("VETOED", "CIRCUIT_OPEN"):
            storage = MagicMock()
            storage.update_review_session = AsyncMock()
            events: list = []
            agent = ExecutiveAgent(
                _make_config(
                    agent_id=f"coo-{terminal_status.lower()}",
                    team_id="exec_coo",
                    agent_role=AgentRole.EXECUTIVE,
                ),
                reviewer_teams=["office_cso", "office_cfo"],
                review_storage=storage,
                event_emitter=lambda event, **kwargs: events.append((event, kwargs)),
            )
            project_id = str(uuid4())
            session_id = str(uuid4())
            summary = ReviewSummary(
                session_id=session_id,
                project_id=project_id,
                document_id=uuid4(),
                doc_type="PDR",
                reviewer_count=2,
            )
            agent._review_sessions[session_id] = summary
            agent._review_parents[session_id] = _make_envelope(
                msg_type=MessageType.DOCUMENT_SUBMIT,
                project_id=project_id,
                payload={"document_id": str(summary.document_id), "doc_type": "PDR"},
            )

            if terminal_status == "VETOED":
                response = ReviewResponse(
                    reviewer_id="cso-terminal",
                    reviewer_role="c_suite",
                    reviewer_team="office_cso",
                    verdict=ReviewVerdict.REJECTED,
                    veto=True,
                )
                await agent._handle_cso_veto(session_id, response, agent._review_parents[session_id])
            else:
                await agent.record_review_timeout(session_id, "cfo-terminal")
                await agent.record_review_timeout(session_id, "cso-terminal")

            update_kwargs = storage.update_review_session.await_args.kwargs
            assert update_kwargs["status"] == terminal_status
            assert update_kwargs["completed_at"] is not None

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
        """Revision limits apply across immutable IDs in one document lineage."""
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

        # The revised artifact has a new immutable ID, but remains in the same
        # project/PDR lineage and must not reset the limit.
        revised_summary = summary.model_copy(update={"document_id": uuid4()})
        await agent._request_revision(revised_summary, parent_env)
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
    async def test_review_request_skips_superseded_document_without_llm(self):
        """Stale review work is acknowledged without blocking the current revision."""
        config = _make_config(
            agent_id="cfo-stale", team_id="office_cfo", agent_role=AgentRole.C_SUITE
        )
        llm = _FakeLLMClient("must not review superseded content")
        tool_client = _FakeToolClient(
            result={
                "id": "doc-v2",
                "version": 2,
                "doc_type": "CDR",
                "blob_key": "project/documents/cdr_v2.md",
            }
        )
        agent = CSuiteAgent(
            config,
            specialization="CFO",
            llm_client=llm,
            tool_client=tool_client,
        )
        router = _patch_router(agent)

        env = _make_envelope(
            msg_type=MessageType.REVIEW_REQUEST,
            sender_id="coo-1",
            sender_team="exec_coo",
            payload={
                "session_id": str(uuid4()),
                "document_id": "doc-v1",
                "doc_type": "CDR",
                "document_payload": {"project_id": "proj-001"},
            },
        )
        agent._current_envelope = env
        agent._budget = BudgetTracker()

        await agent.handle_message(env)

        assert llm.calls == []
        assert router.publish.call_count == 1
        reply = router.publish.call_args[0][0]
        assert reply.payload["document_id"] == "doc-v1"
        assert reply.payload["comments"] == []

    @pytest.mark.asyncio
    async def test_review_request_nacks_when_durable_document_is_unavailable(self):
        """Reviewers must not approve or veto based on metadata alone."""
        llm = _FakeLLMClient("APPROVED")
        agent = CSuiteAgent(
            _make_config(
                agent_id="cfo-fetch-failure",
                team_id="office_cfo",
                agent_role=AgentRole.C_SUITE,
            ),
            specialization="CFO",
            llm_client=llm,
            tool_client=_FakeToolClient(result={"error": "database unavailable"}),
        )
        env = _make_envelope(
            msg_type=MessageType.REVIEW_REQUEST,
            project_id="00000000-0000-4000-a000-000000000125",
            payload={
                "session_id": str(uuid4()),
                "document_id": str(uuid4()),
                "doc_type": "CDR",
                "document_payload": {},
            },
        )

        with pytest.raises(RuntimeError, match="without its durable content"):
            await agent.handle_message(env)

        assert llm.calls == []

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

    def test_cso_does_not_treat_veto_discussion_as_veto(self):
        """A review that explicitly declines a veto must remain non-blocking."""
        config = _make_config(
            agent_id="cso-affirmative-only",
            team_id="office_cso",
            agent_role=AgentRole.C_SUITE,
        )
        agent = CSuiteAgent(config, specialization="CSO")

        comments, verdict, has_veto = agent._parse_review_output(
            "APPROVED_WITH_COMMENTS. Grounds for veto considered: None."
        )

        assert comments[0].veto is False
        assert verdict.value == "APPROVED_WITH_COMMENTS"
        assert has_veto is False

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
    async def test_ceo_human_directive_publishes_chat_response(self):
        """CEO direct human directives publish a RESPONSE visible on the CEO stream."""
        config = _make_config(
            agent_id="ceo", team_id="exec_ceo", agent_role=AgentRole.ORCHESTRATOR
        )
        llm = _FakeLLMClient("I will handle this request.")
        agent = CSuiteAgent(config, specialization="CEO", llm_client=llm)
        router = _patch_router(agent)

        env = _make_envelope(
            msg_type=MessageType.TASK,
            sender_id="human_operator",
            sender_team="orchestrator",
            sender_role=AgentRole.ORCHESTRATOR,
            recipient_team="exec_ceo",
            project_id="operator-direct",
            payload={
                "action": "HUMAN_DIRECTIVE",
                "instruction": "Can you summarize current priorities?",
            },
        )
        agent._current_envelope = env
        agent._budget = BudgetTracker()

        await agent.handle_message(env)

        assert router.publish.call_count == 1
        reply = router.publish.call_args[0][0]
        assert reply.msg_type == MessageType.RESPONSE
        assert reply.sender_id == "ceo"
        assert reply.recipient_team == "exec_ceo"
        assert reply.parent_id == env.message_id
        assert reply.payload["source"] == "human_directive"
        assert "handle this request" in reply.payload["response"]
        assert llm.calls[0]["kwargs"]["tools"] == []
        assert "chat runtime will deliver" in llm.calls[0]["messages"][1]["content"]
        assert "Use available tools" not in llm.calls[0]["messages"][1]["content"]

    @pytest.mark.asyncio
    async def test_ceo_skips_api_owned_human_directive(self):
        config = _make_config(
            agent_id="ceo", team_id="exec_ceo", agent_role=AgentRole.ORCHESTRATOR
        )
        llm = _FakeLLMClient("must not execute")
        agent = CSuiteAgent(config, specialization="CEO", llm_client=llm)
        router = _patch_router(agent)
        env = _make_envelope(
            msg_type=MessageType.TASK,
            sender_id="human_operator",
            sender_team="orchestrator",
            sender_role=AgentRole.ORCHESTRATOR,
            recipient_team="exec_ceo",
            project_id="operator-direct",
            payload={
                "action": "HUMAN_DIRECTIVE",
                "instruction": "Create a project named Once",
                "execution_owner": "orchestrator-api",
            },
        )
        agent._current_envelope = env
        agent._budget = BudgetTracker()

        await agent.handle_message(env)

        assert llm.calls == []
        assert router.publish.call_count == 0

    @pytest.mark.asyncio
    async def test_ceo_start_feasibility_routes_through_assessment(self):
        config = _make_config(
            agent_id="ceo", team_id="exec_ceo", agent_role=AgentRole.ORCHESTRATOR
        )
        agent = CSuiteAgent(config, specialization="CEO", llm_client=_FakeLLMClient())
        agent._directive_think = AsyncMock()
        env = _make_envelope(
            msg_type=MessageType.DIRECTIVE,
            recipient_team="exec_ceo",
            project_id="project-feasibility",
            payload={
                "action": "START_FEASIBILITY",
                "description": "Assess safety and technical viability before approval",
            },
        )

        await agent._handle_directive(env)

        agent._directive_think.assert_awaited_once_with(env, "START_FEASIBILITY")

    @pytest.mark.asyncio
    async def test_ceo_document_stage_is_deterministic(self):
        """CEO document handoffs do not depend on an LLM choosing control tools."""
        config = _make_config(
            agent_id="ceo", team_id="exec_ceo", agent_role=AgentRole.ORCHESTRATOR
        )
        llm = _FakeLLMClient("must not run for a document handoff")
        agent = CSuiteAgent(config, specialization="CEO", llm_client=llm)
        agent._recover_directive_progress = AsyncMock()
        env = _make_envelope(
            msg_type=MessageType.DIRECTIVE,
            recipient_team="exec_ceo",
            project_id="project-cdr",
            payload={
                "action": "START_CDR",
                "triggered_by_event": "cdr_revision_requested",
            },
        )

        await agent._handle_directive(env)

        agent._recover_directive_progress.assert_awaited_once_with(env, "START_CDR")
        assert llm.calls == []

    @pytest.mark.asyncio
    async def test_initial_pdr_contains_complete_review_structure(self):
        """Initial artifacts include all review domains and explicit evidence gaps."""
        config = _make_config(
            agent_id="ceo", team_id="exec_ceo", agent_role=AgentRole.ORCHESTRATOR
        )
        agent = CSuiteAgent(config, specialization="CEO")
        calls: list[tuple[str, dict[str, Any]]] = []

        async def execute_tool(name: str, kwargs: dict[str, Any]) -> dict[str, Any]:
            calls.append((name, kwargs))
            if name == "project.status":
                return {
                    "id": "project-initial-pdr",
                    "name": "Evidence-first system",
                    "description": "Build a governed service with auditable release evidence.",
                    "state": "PDR_CREATION",
                    "config": {
                        "financial_model": {
                            "implementation_estimate_usd": 42000,
                            "source": "CFO estimate 2026-07-19",
                        }
                    },
                }
            if name == "document.get_latest":
                return {"error": "document_not_found"}
            if name == "document.create_draft":
                content = kwargs["content"]
                assert "Objective, scope, and requirements" in content
                assert "Proposed architecture and interfaces" in content
                assert "Security, privacy, and governance requirements" in content
                assert "Risks, decisions, and mitigations" in content
                assert "Verification and readiness evidence" in content
                assert '"implementation_estimate_usd": 42000' in content
                assert "USD 25,000" not in content
                return {"document": {"id": "pdr-v1", "version": 1, "doc_type": "PDR"}}
            if name == "document.submit":
                return {"status": "submitted"}
            if name == "review.start_session":
                return {"status": "published"}
            raise AssertionError(f"unexpected tool {name}")

        agent.execute_tool = AsyncMock(side_effect=execute_tool)
        env = _make_envelope(
            msg_type=MessageType.DIRECTIVE,
            recipient_team="exec_ceo",
            project_id="project-initial-pdr",
            payload={"action": "START_PDR", "triggered_by_event": "feasibility_approved"},
        )

        await agent._recover_directive_progress(env, "START_PDR")

        assert [name for name, _ in calls] == [
            "project.status",
            "document.get_latest",
            "document.create_draft",
            "document.submit",
            "review.start_session",
        ]

    @pytest.mark.asyncio
    async def test_pdr_revision_creates_sourced_reviewable_document_version(self):
        """A PDR revision records evidence gaps without fabricated finances."""
        config = _make_config(
            agent_id="ceo", team_id="exec_ceo", agent_role=AgentRole.ORCHESTRATOR
        )
        agent = CSuiteAgent(
            config,
            specialization="CEO",
            llm_client=_FakeLLMClient("must not run for deterministic recovery"),
        )
        calls: list[tuple[str, dict[str, Any]]] = []

        async def execute_tool(name: str, kwargs: dict[str, Any]) -> dict[str, Any]:
            calls.append((name, kwargs))
            if name == "project.status":
                return {
                    "id": "project-pdr-revision",
                    "name": "Governed runtime preview",
                    "description": "A production-ready governed runtime preview.",
                    "state": "PDR_REVIEW",
                }
            if name == "project.transition":
                return {"status": "completed", "next_state": "PDR_CREATION"}
            if name == "document.get_latest":
                return {"id": "pdr-v1", "version": 1, "doc_type": "PDR"}
            if name == "document.create_draft":
                assert kwargs["doc_type"] == "PDR"
                content = kwargs["content"]
                assert "Objective, scope, and requirements" in content
                assert "Proposed architecture and interfaces" in content
                assert "Security, privacy, and governance requirements" in content
                assert "Financial model" in content
                assert "UNRESOLVED — CFO estimate required" in content
                assert "Requested remediation" in content
                assert "does not claim remediation is complete" in content
                assert "USD 25,000" not in content
                assert "15% ROI" not in content
                return {"document": {"id": "pdr-v2", "version": 2, "doc_type": "PDR"}}
            if name == "document.submit":
                assert kwargs["document_id"] == "pdr-v2"
                return {"status": "submitted"}
            if name == "review.start_session":
                assert kwargs["document_id"] == "pdr-v2"
                return {"status": "started"}
            raise AssertionError(f"unexpected tool {name}")

        agent.execute_tool = AsyncMock(side_effect=execute_tool)
        env = _make_envelope(
            msg_type=MessageType.DIRECTIVE,
            recipient_team="exec_ceo",
            project_id="project-pdr-revision",
            payload={
                "action": "START_PDR",
                "triggered_by_event": "pdr_revision_requested",
                "context": {
                    "revision_requested": True,
                    "reason": "CFO requested budget, ROI, contingency, and sprint estimates.",
                },
            },
        )

        await agent._recover_directive_progress(env, "START_PDR")

        assert [name for name, _ in calls] == [
            "project.status",
            "project.transition",
            "document.get_latest",
            "document.create_draft",
            "document.submit",
            "review.start_session",
        ]
        assert calls[1][1]["event"] == "pdr_revision_requested"

    @pytest.mark.asyncio
    async def test_ceo_human_directive_publishes_terminal_tool_call_message(self):
        config = _make_config(
            agent_id="ceo", team_id="exec_ceo", agent_role=AgentRole.ORCHESTRATOR
        )
        tool_call = ToolCall(
            id="call_notify",
            function=ToolCallFunction(
                name="human.notify",
                arguments='{"message":"Direct answer from notify tool."}',
            ),
        )
        llm = _FakeToolCallLLMClient(tool_call)
        agent = CSuiteAgent(config, specialization="CEO", llm_client=llm)
        router = _patch_router(agent)

        env = _make_envelope(
            msg_type=MessageType.TASK,
            sender_id="human_operator",
            sender_team="orchestrator",
            sender_role=AgentRole.ORCHESTRATOR,
            recipient_team="exec_ceo",
            project_id="operator-direct",
            payload={
                "action": "HUMAN_DIRECTIVE",
                "instruction": "What model are you?",
            },
        )
        agent._current_envelope = env
        agent._budget = BudgetTracker()

        await agent.handle_message(env)

        assert llm.calls[0]["kwargs"]["tools"] == []
        assert router.publish.call_count == 1
        reply = router.publish.call_args[0][0]
        assert reply.payload["response"] == "Direct answer from notify tool."

    @pytest.mark.asyncio
    async def test_ceo_resume_directive_does_not_enter_llm_loop(self):
        """CEO startup RESUME directives should not block live human chat."""
        config = _make_config(
            agent_id="ceo", team_id="exec_ceo", agent_role=AgentRole.ORCHESTRATOR
        )
        llm = _FakeLLMClient("resume response")
        agent = CSuiteAgent(config, specialization="CEO", llm_client=llm)
        router = _patch_router(agent)

        env = _make_envelope(
            msg_type=MessageType.DIRECTIVE,
            sender_id="orchestrator",
            sender_team="orchestrator",
            sender_role=AgentRole.ORCHESTRATOR,
            recipient_team="exec_ceo",
            project_id="00000000-0000-4000-a000-000000000001",
            payload={
                "action": "RESUME",
                "state": "FAILED",
                "context": "System restart - resume from last committed state",
            },
        )
        agent._current_envelope = env
        agent._budget = BudgetTracker()

        await agent.handle_message(env)

        assert llm.calls == []
        assert router.publish.call_count == 0

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
