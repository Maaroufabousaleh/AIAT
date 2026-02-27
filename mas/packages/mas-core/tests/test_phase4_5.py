"""Tests for Phase 4 (Agent Runtime) and Phase 5 (LLM Gateway).

Test classes
------------
TestBudgetTracker       — BudgetTracker caps, consume methods, snapshot/restore
TestLRUSet              — _LRUSet eviction and dedup logic
TestAgentBase           — LRU idempotency, dispatch, think() loop, checkpoint helpers
TestRouterClientHTTP    — publish / broadcast over mocked HTTP (httpx mock)
TestLLMGatewayModels    — ChatResponse, UsageStats parsing helpers
TestLLMGatewayClient    — chat_completion with mocked HTTP, retry on 429/5xx
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from mas_core.agent_runtime.base import AgentBase, _LRUSet
from mas_core.agent_runtime.budget import BudgetExhausted, BudgetTracker
from mas_core.agent_runtime.config import AgentConfig
from mas_core.agent_runtime.router_client import RouterClient
from mas_core.llm_gateway.client import LLMGatewayClient, LLMGatewayError, LLMRateLimited
from mas_core.llm_gateway.models import (
    ChatMessage,
    ChatResponse,
    LLMConfig,
    ToolCall,
    ToolCallFunction,
    UsageStats,
)
from mas_core.protocols.envelope import MessageEnvelope, TaskBudget
from mas_core.protocols.enums import AgentRole, MessageType
from mas_core.protocols.ws import WSMessageFrame


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_envelope(**overrides) -> MessageEnvelope:
    """Build a minimal valid MessageEnvelope for TASK messages."""
    defaults = dict(
        msg_type=MessageType.TASK,
        sender_id="test_sender",
        sender_role=AgentRole.ADMIN,
        sender_team="dept_system",
        recipient_team="dept_production",
        project_id="proj-001",
        payload={"task": "do something"},
    )
    defaults.update(overrides)
    return MessageEnvelope(**defaults)


def _make_config(**overrides) -> AgentConfig:
    """Build an AgentConfig without hitting env vars."""
    defaults = dict(
        agent_id="test_agent",
        team_id="dept_system",
        agent_role=AgentRole.WORKER,
        agent_secret="secret123",
        router_url="http://fake-router:8000",
    )
    defaults.update(overrides)
    # Construct directly, bypassing env loading
    return AgentConfig.model_construct(**defaults)


def _make_frame(envelope: MessageEnvelope, entry_id: str = "1234-0") -> WSMessageFrame:
    return WSMessageFrame(
        entry_id=entry_id,
        envelope=envelope,
        stream="stream:dept_system",
        retry_count=0,
    )


# ---------------------------------------------------------------------------
# Concrete AgentBase subclass for testing
# ---------------------------------------------------------------------------


class _EchoAgent(AgentBase):
    """Minimal AgentBase implementation that records received envelopes."""

    def __init__(self, config: AgentConfig, storage=None, llm_client=None, tool_executor=None):
        super().__init__(config, storage, llm_client=llm_client, tool_executor=tool_executor)
        self.handled: list[MessageEnvelope] = []
        self.budgets_seen: list[BudgetTracker | None] = []
        self.raise_on_next: Exception | None = None

    async def handle_message(self, envelope: MessageEnvelope) -> None:
        if self.raise_on_next:
            exc = self.raise_on_next
            self.raise_on_next = None
            raise exc
        self.budgets_seen.append(self._budget)
        self.handled.append(envelope)


class _FakeLLMClient:
    """Simple deterministic LLM stub for AgentBase tests."""

    def __init__(self, responses: list[ChatResponse]):
        self._responses = list(responses)
        self.calls: list[dict] = []
        self.started = False

    async def start(self):
        self.started = True

    async def stop(self):
        self.started = False

    async def chat_completion(self, messages, **kwargs) -> ChatResponse:
        self.calls.append({"messages": messages, "kwargs": kwargs})
        if self._responses:
            return self._responses.pop(0)
        return ChatResponse(
            message=ChatMessage(role="assistant", content="fallback"),
            usage=UsageStats(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        )


def _assistant_response(
    *,
    content: str | None = "Done.",
    finish_reason: str = "stop",
    tool_calls: list[ToolCall] | None = None,
    prompt_tokens: int = 5,
    completion_tokens: int = 3,
) -> ChatResponse:
    return ChatResponse(
        model="gpt-4o",
        finish_reason=finish_reason,
        message=ChatMessage(role="assistant", content=content, tool_calls=tool_calls),
        tool_calls=tool_calls or [],
        usage=UsageStats(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        ),
    )


# ===========================================================================
# TestBudgetTracker
# ===========================================================================


class TestBudgetTracker:
    def test_uncapped_budget_never_raises(self):
        bt = BudgetTracker()
        for _ in range(100):
            bt.consume_llm_call()
            bt.consume_tool_call()
            bt.consume_subtask()
        assert bt.ok_to_continue()

    def test_llm_cap_enforced(self):
        bt = BudgetTracker(max_llm_calls=3)
        bt.consume_llm_call()
        bt.consume_llm_call()
        bt.consume_llm_call()
        with pytest.raises(BudgetExhausted, match="LLM"):
            bt.consume_llm_call()

    def test_tool_cap_enforced(self):
        bt = BudgetTracker(max_tool_calls=1)
        bt.consume_tool_call()
        with pytest.raises(BudgetExhausted, match="[Tt]ool"):
            bt.consume_tool_call()

    def test_subtask_cap_enforced(self):
        bt = BudgetTracker(max_subtasks=2)
        bt.consume_subtask()
        bt.consume_subtask()
        with pytest.raises(BudgetExhausted, match="Subtask"):
            bt.consume_subtask()

    def test_cost_cap_enforced(self):
        bt = BudgetTracker(max_cost_usd=0.01)
        bt.consume_llm_call(cost_usd=0.005)
        bt.consume_llm_call(cost_usd=0.005)
        with pytest.raises(BudgetExhausted, match="Cost"):
            bt.consume_llm_call(cost_usd=0.001)

    def test_deadline_exceeded(self):
        past = datetime.now(tz=timezone.utc) - timedelta(seconds=1)
        bt = BudgetTracker(deadline=past)
        assert not bt.ok_to_continue()
        with pytest.raises(BudgetExhausted, match="deadline"):
            bt.check_before_llm_call()

    def test_snapshot_restore_roundtrip(self):
        bt = BudgetTracker(max_llm_calls=10, max_cost_usd=1.0)
        bt.consume_llm_call(tokens_in=100, tokens_out=50, cost_usd=0.03)
        bt.consume_tool_call()
        snap = bt.snapshot()
        bt2 = BudgetTracker.restore_snapshot(snap)
        assert bt2.llm_calls_used == 1
        assert bt2.tool_calls_used == 1
        assert abs(bt2.cost_usd_used - 0.03) < 1e-9
        assert bt2.tokens_in_used == 100
        assert bt2.tokens_out_used == 50

    def test_from_task_budget_none(self):
        bt = BudgetTracker.from_task_budget(None)
        assert bt.max_llm_calls is None
        assert bt.ok_to_continue()

    def test_from_task_budget_values(self):
        tb = TaskBudget(max_llm_calls=5, max_tool_calls=10, max_cost_usd=2.5)
        bt = BudgetTracker.from_task_budget(tb)
        assert bt.max_llm_calls == 5
        assert bt.max_tool_calls == 10
        assert abs(bt.max_cost_usd - 2.5) < 1e-9

    def test_check_before_llm_call_not_raise_when_ok(self):
        bt = BudgetTracker(max_llm_calls=5)
        bt.check_before_llm_call()  # Should not raise

    def test_check_before_tool_call_not_raise_when_ok(self):
        bt = BudgetTracker(max_tool_calls=5)
        bt.check_before_tool_call()  # Should not raise


# ===========================================================================
# TestLRUSet
# ===========================================================================


class TestLRUSet:
    def test_basic_contains(self):
        lru = _LRUSet(3)
        lru.add("a")
        assert "a" in lru
        assert "b" not in lru

    def test_evicts_oldest_at_capacity(self):
        lru = _LRUSet(3)
        lru.add("a")
        lru.add("b")
        lru.add("c")
        lru.add("d")  # evicts "a"
        assert "a" not in lru
        assert "d" in lru

    def test_re_add_promotes_to_end(self):
        lru = _LRUSet(3)
        lru.add("a")
        lru.add("b")
        lru.add("c")
        lru.add("a")  # promotes "a" to end; evicts "b" next
        lru.add("d")  # evicts "b"
        assert "b" not in lru
        assert "a" in lru

    def test_len(self):
        lru = _LRUSet(5)
        assert len(lru) == 0
        lru.add("x")
        assert len(lru) == 1


# ===========================================================================
# TestAgentBase
# ===========================================================================


class TestAgentBase:
    def _make_agent(
        self,
        *,
        llm_client: _FakeLLMClient | None = None,
        tool_executor=None,
        **config_overrides,
    ) -> _EchoAgent:
        config = _make_config(**config_overrides)
        llm = llm_client or _FakeLLMClient([_assistant_response()])
        return _EchoAgent(config, llm_client=llm, tool_executor=tool_executor)

    @pytest.mark.asyncio
    async def test_dispatch_calls_handle_message(self):
        agent = self._make_agent()
        env = _make_envelope()
        frame = _make_frame(env)
        await agent._dispatch(frame)
        assert env in agent.handled

    @pytest.mark.asyncio
    async def test_dispatch_skips_duplicate_message(self):
        agent = self._make_agent()
        env = _make_envelope()
        frame = _make_frame(env)
        await agent._dispatch(frame)
        await agent._dispatch(frame)  # second delivery — LRU should skip
        assert len(agent.handled) == 1

    @pytest.mark.asyncio
    async def test_dispatch_does_not_add_to_lru_on_exception(self):
        """Failures should NACK and allow re-delivery of the same message."""
        agent = self._make_agent()
        env = _make_envelope()
        frame = _make_frame(env)
        agent.raise_on_next = RuntimeError("handler error")
        with pytest.raises(RuntimeError, match="handler error"):
            await agent._dispatch(frame)
        # Second delivery — LRU hit, skips processing (no exception, no handled)
        await agent._dispatch(frame)
        assert len(agent.handled) == 1

    @pytest.mark.asyncio
    async def test_dispatch_uses_budget_defaults_when_envelope_has_no_budget(self):
        defaults = TaskBudget(max_llm_calls=7, max_tool_calls=3)
        agent = self._make_agent(budget_defaults=defaults)
        env = _make_envelope(budget=None)
        frame = _make_frame(env)

        await agent._dispatch(frame)

        assert agent.budgets_seen
        assert agent.budgets_seen[-1] is not None
        assert agent.budgets_seen[-1].max_llm_calls == 7
        assert agent.budgets_seen[-1].max_tool_calls == 3

    @pytest.mark.asyncio
    async def test_dispatch_envelope_budget_overrides_defaults(self):
        defaults = TaskBudget(max_llm_calls=7)
        agent = self._make_agent(budget_defaults=defaults)
        env = _make_envelope(budget=TaskBudget(max_llm_calls=2))
        frame = _make_frame(env)

        await agent._dispatch(frame)

        assert agent.budgets_seen
        assert agent.budgets_seen[-1] is not None
        assert agent.budgets_seen[-1].max_llm_calls == 2

    @pytest.mark.asyncio
    async def test_think_appends_assistant_message(self):
        llm = _FakeLLMClient([_assistant_response(content="hello from llm")])
        agent = self._make_agent(llm_client=llm)
        # Set a minimal current_envelope so save_checkpoint can run
        agent._current_envelope = _make_envelope()
        messages = [{"role": "user", "content": "hello"}]
        result = await agent.think(messages=messages)
        assert result[0]["content"] == "hello"
        assert result[-1]["role"] == "assistant"
        assert result[-1]["content"] == "hello from llm"
        assert len(llm.calls) == 1

    @pytest.mark.asyncio
    async def test_think_stops_on_budget_exhausted(self):
        """think() exits cleanly when budget is pre-exhausted."""
        llm = _FakeLLMClient([_assistant_response(content="should-not-run")])
        agent = self._make_agent(llm_client=llm)
        agent._current_envelope = _make_envelope()
        past = datetime.now(tz=timezone.utc) - timedelta(seconds=10)
        agent._budget = BudgetTracker(deadline=past)
        messages = [{"role": "user", "content": "x"}]
        result = await agent.think(messages=messages)
        # Returns messages (possibly same list) without crashing
        assert isinstance(result, list)
        assert len(llm.calls) == 0

    @pytest.mark.asyncio
    async def test_checkpoint_helpers_no_storage(self):
        """save/load/delete_checkpoint are no-ops when storage=None."""
        agent = self._make_agent()
        agent._current_envelope = _make_envelope()
        # Should not raise
        await agent.save_checkpoint({"messages": [], "iteration": 0})
        result = await agent.load_checkpoint()
        assert result is None
        await agent.delete_checkpoint()

    @pytest.mark.asyncio
    async def test_checkpoint_helpers_with_storage(self):
        """save/load/delete_checkpoint delegate to storage object."""
        storage = MagicMock()
        storage.save_checkpoint = AsyncMock()
        storage.load_checkpoint = AsyncMock(return_value={"messages": [], "iteration": 3})
        storage.delete_checkpoint = AsyncMock()

        config = _make_config()
        agent = _EchoAgent(config, storage=storage, llm_client=_FakeLLMClient([_assistant_response()]))
        agent._current_envelope = _make_envelope(project_id="proj-001")

        await agent.save_checkpoint({"messages": [], "iteration": 0})
        storage.save_checkpoint.assert_called_once()

        result = await agent.load_checkpoint()
        assert result["iteration"] == 3

        await agent.delete_checkpoint()
        storage.delete_checkpoint.assert_called_once()

    def test_restore_from_checkpoint_sets_state(self):
        agent = self._make_agent()
        agent._budget = BudgetTracker(max_llm_calls=10)
        cp = {
            "messages": [{"role": "user", "content": "x"}],
            "iteration": 5,
            "budget_snapshot": BudgetTracker(max_llm_calls=10, llm_calls_used=3).snapshot(),
        }
        agent.restore_from_checkpoint(cp)
        assert agent._checkpoint["iteration"] == 5
        assert agent._budget.llm_calls_used == 3

    @pytest.mark.asyncio
    async def test_think_resumes_from_checkpoint(self):
        storage = MagicMock()
        storage.save_checkpoint = AsyncMock()
        storage.load_checkpoint = AsyncMock(
            return_value={
                "messages": [{"role": "user", "content": "resumed"}],
                "iteration": 7,
                "budget_snapshot": BudgetTracker(max_llm_calls=20, llm_calls_used=7).snapshot(),
            }
        )
        storage.delete_checkpoint = AsyncMock()

        config = _make_config(max_think_iterations=20)
        llm = _FakeLLMClient([_assistant_response(content="after resume")])
        agent = _EchoAgent(config, storage=storage, llm_client=llm)
        agent._current_envelope = _make_envelope(project_id="proj-001")

        messages = [{"role": "user", "content": "original"}]
        result = await agent.think(messages=messages, resume=True)
        # Messages should be replaced with the checkpoint's version
        assert result[0]["content"] == "resumed"
        assert result[-1]["content"] == "after resume"
        storage.delete_checkpoint.assert_called_once()

    @pytest.mark.asyncio
    async def test_think_executes_tool_calls_and_updates_budget(self):
        tool_call = ToolCall(
            id="call_1",
            function=ToolCallFunction(name="web.search", arguments='{"q":"ai"}'),
        )
        llm = _FakeLLMClient(
            [
                _assistant_response(content=None, finish_reason="tool_calls", tool_calls=[tool_call]),
                _assistant_response(content="final answer"),
            ]
        )
        tool_executor = AsyncMock(return_value={"hits": ["one"]})
        agent = self._make_agent(llm_client=llm, tool_executor=tool_executor)
        agent._current_envelope = _make_envelope(project_id="proj-001")

        messages = [{"role": "user", "content": "find ai"}]
        result = await agent.think(messages=messages)

        assert result[-1]["content"] == "final answer"
        assert any(m.get("role") == "tool" for m in result)
        tool_executor.assert_called_once_with("web.search", {"q": "ai"})
        assert agent._budget is not None
        assert agent._budget.llm_calls_used == 2
        assert agent._budget.tool_calls_used == 1


# ===========================================================================
# TestRouterClientHTTP
# ===========================================================================


class TestRouterClientHTTP:
    """Tests for RouterClient publish/broadcast over mocked httpx."""

    @pytest.mark.asyncio
    async def test_publish_success(self):
        from mas_core.agent_runtime.router_client import RouterClient

        env = _make_envelope()

        async def mock_post(_self, url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {"entry_id": "1234-0"}
            resp.text = ""
            return resp

        client = RouterClient(
            router_url="http://fake:8000",
            agent_id="a1",
            agent_secret="s1",
        )
        import httpx
        with patch.object(httpx.AsyncClient, "post", new=mock_post):
            await client.start()
            entry_id = await client.publish(env)
            assert entry_id == "1234-0"
            await client.stop()

    @pytest.mark.asyncio
    async def test_publish_duplicate_raises(self):
        from mas_core.agent_runtime.router_client import RouterClient, RouterDuplicateMessage

        env = _make_envelope()

        async def mock_post(_self, url, **kwargs):
            resp = MagicMock()
            resp.status_code = 409
            resp.text = "duplicate"
            resp.json.return_value = {}
            return resp

        client = RouterClient(
            router_url="http://fake:8000",
            agent_id="a1",
            agent_secret="s1",
        )
        import httpx
        with patch.object(httpx.AsyncClient, "post", new=mock_post):
            await client.start()
            with pytest.raises(RouterDuplicateMessage):
                await client.publish(env)
            await client.stop()

    @pytest.mark.asyncio
    async def test_broadcast_success(self):
        from mas_core.agent_runtime.router_client import RouterClient

        # BROADCAST type is exempt from project_id and routing checks
        env = MessageEnvelope(
            msg_type=MessageType.BROADCAST,
            sender_id="ceo_agent",
            sender_role=AgentRole.ORCHESTRATOR,
            sender_team="exec_ceo",
            project_id="proj-001",
        )

        async def mock_post(_self, url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {"exec_ceo": "1-0", "dept_system": "2-0"}
            resp.text = ""
            return resp

        client = RouterClient(
            router_url="http://fake:8000",
            agent_id="a1",
            agent_secret="s1",
        )
        import httpx
        with patch.object(httpx.AsyncClient, "post", new=mock_post):
            await client.start()
            result = await client.broadcast(env)
            assert "exec_ceo" in result
            await client.stop()

    @pytest.mark.asyncio
    async def test_publish_router_error_raises(self):
        from mas_core.agent_runtime.router_client import RouterClient, RouterError

        env = _make_envelope()

        async def mock_post(_self, url, **kwargs):
            resp = MagicMock()
            resp.status_code = 503
            resp.text = "service unavailable"
            return resp

        client = RouterClient(
            router_url="http://fake:8000",
            agent_id="a1",
            agent_secret="s1",
        )
        import httpx
        with patch.object(httpx.AsyncClient, "post", new=mock_post):
            await client.start()
            with pytest.raises(RouterError) as exc_info:
                await client.publish(env)
            assert exc_info.value.status_code == 503
            await client.stop()

    def test_require_http_before_start_raises(self):
        from mas_core.agent_runtime.router_client import RouterClient
        client = RouterClient(
            router_url="http://fake:8000",
            agent_id="a1",
            agent_secret="s1",
        )
        with pytest.raises(RuntimeError, match="not started"):
            client._require_http()


class TestRouterClientWSCompatibility:
    def test_ws_headers_kwargs_uses_additional_headers_when_supported(self):
        class _FakeWS:
            @staticmethod
            def connect(uri, *, additional_headers=None):  # noqa: ANN001
                return None

        kwargs = RouterClient._ws_connect_headers_kwargs(_FakeWS, "Bearer token")
        assert kwargs == {"additional_headers": {"Authorization": "Bearer token"}}

    def test_ws_headers_kwargs_falls_back_to_extra_headers(self):
        class _FakeWS:
            @staticmethod
            def connect(uri, *, extra_headers=None):  # noqa: ANN001
                return None

        kwargs = RouterClient._ws_connect_headers_kwargs(_FakeWS, "Bearer token")
        assert kwargs == {"extra_headers": {"Authorization": "Bearer token"}}


# ===========================================================================
# TestLLMGatewayModels
# ===========================================================================


class TestLLMGatewayModels:
    def test_chat_response_has_tool_calls_false(self):
        msg = ChatMessage(role="assistant", content="hello")
        resp = ChatResponse(message=msg, finish_reason="stop")
        assert resp.has_tool_calls is False
        assert resp.text == "hello"

    def test_chat_response_has_tool_calls_true(self):
        tc = ToolCall(id="tc1", function=ToolCallFunction(name="web.search", arguments="{}"))
        msg = ChatMessage(role="assistant", tool_calls=[tc])
        resp = ChatResponse(message=msg, tool_calls=[tc], finish_reason="tool_calls")
        assert resp.has_tool_calls is True

    def test_usage_stats_cost_estimate(self):
        usage = UsageStats(prompt_tokens=1_000_000, completion_tokens=1_000_000)
        # $5 / 1M in + $15 / 1M out = $20 total
        assert abs(usage.estimated_cost_usd - 20.0) < 0.01

    def test_parse_response_basic(self):
        raw = {
            "id": "chatcmpl-abc",
            "model": "gpt-4o",
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "Hello!"},
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
        resp = LLMGatewayClient._parse_response(raw)
        assert resp.text == "Hello!"
        assert resp.usage.total_tokens == 15
        assert resp.finish_reason == "stop"

    def test_parse_response_tool_call(self):
        raw = {
            "id": "chatcmpl-xyz",
            "model": "gpt-4o",
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "web.search",
                                    "arguments": '{"query": "Python asyncio"}',
                                },
                            }
                        ],
                    },
                }
            ],
            "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
        }
        resp = LLMGatewayClient._parse_response(raw)
        assert resp.has_tool_calls
        assert resp.tool_calls[0].function.name == "web.search"
        assert resp.finish_reason == "tool_calls"


# ===========================================================================
# TestLLMGatewayClient
# ===========================================================================


class TestLLMGatewayClient:
    def _make_config(self, **overrides) -> LLMConfig:
        defaults = dict(
            gateway_url="http://fake-llm:8080",
            default_model="gpt-4o",
            api_key="test-key",
            max_retries=2,
            retry_min_wait_s=0.01,
            retry_max_wait_s=0.05,
        )
        defaults.update(overrides)
        return LLMConfig.model_construct(**defaults)

    def _ok_response(self) -> MagicMock:
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "id": "chatcmpl-ok",
            "model": "gpt-4o",
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "Done."},
                }
            ],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        }
        return resp

    @pytest.mark.asyncio
    async def test_chat_completion_success(self):
        config = self._make_config()
        client = LLMGatewayClient(config)
        import httpx

        async def mock_post(_self, url, **kwargs):
            return self._ok_response()

        with patch.object(httpx.AsyncClient, "post", new=mock_post):
            async with client:
                resp = await client.chat_completion([{"role": "user", "content": "hi"}])
        assert resp.text == "Done."
        assert resp.usage.total_tokens == 8

    @pytest.mark.asyncio
    async def test_chat_completion_stream_success(self):
        config = self._make_config()
        client = LLMGatewayClient(config)
        import httpx

        class _MockStreamResponse:
            status_code = 200

            async def aread(self):
                return b""

            async def aiter_lines(self):
                yield 'data: {"id":"chatcmpl-stream","model":"gpt-4o","choices":[{"delta":{"role":"assistant","content":"Hel"},"finish_reason":null}]}'
                yield 'data: {"choices":[{"delta":{"content":"lo"},"finish_reason":"stop"}],"usage":{"prompt_tokens":2,"completion_tokens":2,"total_tokens":4}}'
                yield "data: [DONE]"

        class _MockStreamContext:
            async def __aenter__(self):
                return _MockStreamResponse()

            async def __aexit__(self, exc_type, exc, tb):
                return False

        def mock_stream(_self, method, url, **kwargs):
            assert method == "POST"
            return _MockStreamContext()

        with patch.object(httpx.AsyncClient, "stream", new=mock_stream):
            async with client:
                resp = await client.chat_completion(
                    [{"role": "user", "content": "hi"}],
                    stream=True,
                )
        assert resp.text == "Hello"
        assert resp.usage.total_tokens == 4
        assert resp.finish_reason == "stop"

    @pytest.mark.asyncio
    async def test_chat_completion_stream_tool_calls(self):
        config = self._make_config()
        client = LLMGatewayClient(config)
        import httpx

        class _MockStreamResponse:
            status_code = 200

            async def aread(self):
                return b""

            async def aiter_lines(self):
                yield 'data: {"id":"chatcmpl-stream2","model":"gpt-4o","choices":[{"delta":{"role":"assistant","tool_calls":[{"index":0,"id":"call_1","type":"function","function":{"name":"web.search","arguments":"{\\"q\\": \\"a"}}]},"finish_reason":null}]}'
                yield 'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"i\\"}"}}]},"finish_reason":"tool_calls"}],"usage":{"prompt_tokens":3,"completion_tokens":1,"total_tokens":4}}'
                yield "data: [DONE]"

        class _MockStreamContext:
            async def __aenter__(self):
                return _MockStreamResponse()

            async def __aexit__(self, exc_type, exc, tb):
                return False

        def mock_stream(_self, method, url, **kwargs):
            return _MockStreamContext()

        with patch.object(httpx.AsyncClient, "stream", new=mock_stream):
            async with client:
                resp = await client.chat_completion(
                    [{"role": "user", "content": "use tool"}],
                    stream=True,
                )
        assert resp.has_tool_calls is True
        assert resp.tool_calls[0].function.name == "web.search"
        assert resp.tool_calls[0].function.arguments == '{"q": "ai"}'

    @pytest.mark.asyncio
    async def test_chat_completion_retries_on_429(self):
        config = self._make_config(max_retries=2)
        client = LLMGatewayClient(config)
        import httpx

        call_count = 0

        async def mock_post(_self, url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                resp = MagicMock()
                resp.status_code = 429
                resp.text = "rate limited"
                return resp
            return self._ok_response()

        with patch.object(httpx.AsyncClient, "post", new=mock_post):
            async with client:
                resp = await client.chat_completion([{"role": "user", "content": "hi"}])
        assert resp.text == "Done."
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_chat_completion_raises_after_max_retries(self):
        config = self._make_config(max_retries=1)
        client = LLMGatewayClient(config)
        import httpx

        async def mock_post(_self, url, **kwargs):
            resp = MagicMock()
            resp.status_code = 500
            resp.text = "server error"
            return resp

        with patch.object(httpx.AsyncClient, "post", new=mock_post):
            async with client:
                with pytest.raises(LLMGatewayError):
                    await client.chat_completion([{"role": "user", "content": "hi"}])

    @pytest.mark.asyncio
    async def test_chat_completion_raises_on_403(self):
        """4xx (non-429) errors are not retried."""
        config = self._make_config()
        client = LLMGatewayClient(config)
        import httpx

        call_count = 0

        async def mock_post(_self, url, **kwargs):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            resp.status_code = 403
            resp.text = "forbidden"
            return resp

        with patch.object(httpx.AsyncClient, "post", new=mock_post):
            async with client:
                with pytest.raises(LLMGatewayError) as exc_info:
                    await client.chat_completion([{"role": "user", "content": "hi"}])
        assert exc_info.value.status_code == 403
        assert call_count == 1  # Not retried

    @pytest.mark.asyncio
    async def test_require_http_before_start_raises(self):
        config = self._make_config()
        client = LLMGatewayClient(config)
        with pytest.raises(RuntimeError, match="not started"):
            client._require_http()

    @pytest.mark.asyncio
    async def test_context_manager_starts_and_stops(self):
        config = self._make_config()
        client = LLMGatewayClient(config)
        async with client:
            assert client._http is not None
        assert client._http is None

