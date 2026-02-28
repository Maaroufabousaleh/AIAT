"""Phase 6 tests — tool-service registry, circuit breaker, rate limiter, cache, routes."""

from __future__ import annotations

import anyio

import pytest

from mas_core.protocols.enums import AgentRole
from mas_core.protocols.tool import CircuitState, ToolRequest


# ═══════════════════════════════════════════════════════════════════════════
# Registry: tool lookup, manifest, execute pipeline
# ═══════════════════════════════════════════════════════════════════════════


class TestToolRegistry:
    """Tests for ToolRegistry dispatch logic."""

    def test_all_tools_registered(self, make_registry):
        registry = make_registry()
        assert len(registry.tool_names) >= 30, f"Expected 30+ tools, got {len(registry.tool_names)}"

    def test_manifest_has_required_fields(self, make_registry):
        registry = make_registry()
        manifest = registry.get_manifest()
        for entry in manifest:
            assert "tool_name" in entry
            assert "tool_group" in entry
            assert "description" in entry
            assert "allowed_roles" in entry

    @pytest.mark.anyio
    async def test_execute_web_search_as_worker(self, make_registry):
        registry = make_registry()
        req = ToolRequest(
            caller_id="agent-worker-1",
            caller_role=AgentRole.WORKER,
            tool_name="web_search",
            tool_kwargs={"query": "test"},
        )
        resp = await registry.execute(req)
        assert resp.success is True
        assert resp.tool_name == "web_search"
        assert "results" in resp.result

    @pytest.mark.anyio
    async def test_execute_unknown_tool_returns_not_found(self, make_registry):
        registry = make_registry()
        req = ToolRequest(
            caller_id="agent-1",
            caller_role=AgentRole.ORCHESTRATOR,
            tool_name="nonexistent.tool",
            tool_kwargs={},
        )
        resp = await registry.execute(req)
        assert resp.success is False
        assert resp.error_code == "TOOL_NOT_FOUND"

    @pytest.mark.anyio
    async def test_execute_forbidden_role(self, make_registry):
        """SUB_AGENT should not be able to call project.create (ORCHESTRATOR only)."""
        registry = make_registry()
        req = ToolRequest(
            caller_id="sub-agent-1",
            caller_role=AgentRole.SUB_AGENT,
            tool_name="project.create",
            tool_kwargs={},
        )
        resp = await registry.execute(req)
        assert resp.success is False
        assert resp.error_code == "FORBIDDEN"

    @pytest.mark.anyio
    async def test_orchestrator_can_call_any_tool(self, make_registry):
        """Orchestrator has unrestricted tool access."""
        registry = make_registry()
        for tool_name in ["web_search", "project.create", "infra.provision", "sprint.create"]:
            req = ToolRequest(
                caller_id="ceo-orchestrator",
                caller_role=AgentRole.ORCHESTRATOR,
                tool_name=tool_name,
                tool_kwargs={},
            )
            resp = await registry.execute(req)
            assert resp.success is True, f"Orchestrator should be able to call {tool_name}: {resp.error}"

    @pytest.mark.anyio
    async def test_worker_blocked_from_infra_tools(self, make_registry):
        """Workers should be blocked from infra.provision."""
        registry = make_registry()
        req = ToolRequest(
            caller_id="worker-1",
            caller_role=AgentRole.WORKER,
            tool_name="infra.provision",
            tool_kwargs={},
        )
        resp = await registry.execute(req)
        assert resp.success is False
        assert resp.error_code == "FORBIDDEN"

    @pytest.mark.anyio
    async def test_sub_agent_can_download_blob(self, make_registry):
        """Sub-agents have blob.download access."""
        registry = make_registry()
        req = ToolRequest(
            caller_id="sub-1",
            caller_role=AgentRole.SUB_AGENT,
            tool_name="blob.download",
            tool_kwargs={"bucket": "test", "key": "file.txt"},
        )
        resp = await registry.execute(req)
        assert resp.success is True

    @pytest.mark.anyio
    async def test_response_has_trace_ids(self, make_registry):
        registry = make_registry()
        req = ToolRequest(
            caller_id="agent-1",
            caller_role=AgentRole.ORCHESTRATOR,
            tool_name="web_search",
            tool_kwargs={"query": "test"},
            trace_id="trace-abc",
            span_id="span-123",
        )
        resp = await registry.execute(req)
        assert resp.trace_id == "trace-abc"
        assert resp.span_id == "span-123"


# ═══════════════════════════════════════════════════════════════════════════
# Circuit Breaker
# ═══════════════════════════════════════════════════════════════════════════


class TestCircuitBreaker:
    """Tests for the per-tool circuit breaker."""

    @pytest.mark.anyio
    async def test_starts_closed(self):
        from tool_service.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker("test_tool")
        assert cb.state == CircuitState.CLOSED
        assert await cb.allow_request() is True

    @pytest.mark.anyio
    async def test_opens_after_threshold(self):
        from tool_service.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker("test_tool", failure_threshold=3, failure_window=60.0)
        for _ in range(3):
            await cb.record_failure()
        assert cb.state == CircuitState.OPEN
        assert await cb.allow_request() is False

    @pytest.mark.anyio
    async def test_transitions_to_half_open(self):
        from tool_service.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker("test_tool", failure_threshold=1, open_duration=0.01)
        await cb.record_failure()
        assert cb.state == CircuitState.OPEN
        await anyio.sleep(0.02)
        assert cb.state == CircuitState.HALF_OPEN
        assert await cb.allow_request() is True

    @pytest.mark.anyio
    async def test_half_open_success_closes(self):
        from tool_service.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker("test_tool", failure_threshold=1, open_duration=0.01)
        await cb.record_failure()
        await anyio.sleep(0.02)
        assert cb.state == CircuitState.HALF_OPEN
        await cb.record_success()
        assert cb.state == CircuitState.CLOSED

    @pytest.mark.anyio
    async def test_half_open_failure_reopens(self):
        from tool_service.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker("test_tool", failure_threshold=1, open_duration=0.01)
        await cb.record_failure()
        await anyio.sleep(0.02)
        assert cb.state == CircuitState.HALF_OPEN
        await cb.record_failure()
        assert cb.state == CircuitState.OPEN

    @pytest.mark.anyio
    async def test_reset(self):
        from tool_service.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker("test_tool", failure_threshold=1)
        await cb.record_failure()
        assert cb.state == CircuitState.OPEN
        await cb.reset()
        assert cb.state == CircuitState.CLOSED

    def test_to_dict(self):
        from tool_service.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker("my_tool")
        d = cb.to_dict()
        assert d["tool"] == "my_tool"
        assert d["state"] == "CLOSED"


# ═══════════════════════════════════════════════════════════════════════════
# Rate Limiter
# ═══════════════════════════════════════════════════════════════════════════


class TestRateLimiter:
    """Tests for the per-group rate limiter."""

    @pytest.mark.anyio
    async def test_allows_within_limit(self):
        from mas_tools_sdk.groups import ToolGroup
        from tool_service.rate_limiter import RateLimiterPool

        pool = RateLimiterPool()
        allowed, remaining, _ = await pool.acquire(ToolGroup.WEB)
        assert allowed is True

    @pytest.mark.anyio
    async def test_get_rate(self):
        from mas_tools_sdk.groups import GROUP_RATE_LIMITS, ToolGroup
        from tool_service.rate_limiter import RateLimiterPool

        pool = RateLimiterPool()
        assert pool.get_rate(ToolGroup.WEB) == GROUP_RATE_LIMITS[ToolGroup.WEB]


# ═══════════════════════════════════════════════════════════════════════════
# Cache
# ═══════════════════════════════════════════════════════════════════════════


class TestToolCache:
    """Tests for the Redis cache module (mocked Redis)."""

    @pytest.mark.anyio
    async def test_cache_miss_returns_none(self):
        from unittest.mock import AsyncMock

        from tool_service.cache import ToolCache

        mock_redis = AsyncMock()
        mock_redis.get.return_value = None
        cache = ToolCache(mock_redis)
        result = await cache.get("web_search", {"query": "test"})
        assert result is None

    @pytest.mark.anyio
    async def test_cache_hit_returns_value(self):
        import json
        from unittest.mock import AsyncMock

        from tool_service.cache import ToolCache

        mock_redis = AsyncMock()
        mock_redis.get.return_value = json.dumps({"results": [1, 2, 3]})
        cache = ToolCache(mock_redis)
        result = await cache.get("web_search", {"query": "test"})
        assert result == {"results": [1, 2, 3]}

    @pytest.mark.anyio
    async def test_cache_set_calls_redis(self):
        from unittest.mock import AsyncMock

        from tool_service.cache import ToolCache

        mock_redis = AsyncMock()
        cache = ToolCache(mock_redis)
        await cache.set("web_search", {"query": "x"}, {"results": []}, ttl=60)
        mock_redis.setex.assert_called_once()

    @pytest.mark.anyio
    async def test_cache_set_skipped_for_zero_ttl(self):
        from unittest.mock import AsyncMock

        from tool_service.cache import ToolCache

        mock_redis = AsyncMock()
        cache = ToolCache(mock_redis)
        await cache.set("file_write", {}, {"ok": True}, ttl=0)
        mock_redis.setex.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════
# Manifest (SDK)
# ═══════════════════════════════════════════════════════════════════════════


class TestManifest:
    """Tests for the mas-tools-sdk manifest."""

    def test_manifest_not_empty(self):
        from mas_tools_sdk.manifest import TOOL_MANIFEST

        assert len(TOOL_MANIFEST) >= 30

    def test_all_groups_represented(self):
        from mas_tools_sdk.groups import ToolGroup
        from mas_tools_sdk.manifest import TOOL_MANIFEST

        groups_present = {entry["tool_group"] for entry in TOOL_MANIFEST.values()}
        for group in ToolGroup:
            assert group.value in groups_present, f"Group {group.value} missing from manifest"

    def test_web_search_in_manifest(self):
        from mas_tools_sdk.manifest import TOOL_MANIFEST

        assert "web_search" in TOOL_MANIFEST
        entry = TOOL_MANIFEST["web_search"]
        assert entry["tool_group"] == "web"

    def test_project_create_restricted_to_orchestrator(self):
        from mas_tools_sdk.manifest import TOOL_MANIFEST

        entry = TOOL_MANIFEST["project.create"]
        role_values = [r.value if hasattr(r, "value") else r for r in entry["allowed_roles"]]
        assert "orchestrator" in role_values
        assert "worker" not in role_values


# ═══════════════════════════════════════════════════════════════════════════
# HTTP integration (via ASGI client)
# ═══════════════════════════════════════════════════════════════════════════


class TestHTTPIntegration:
    """Integration tests using the full FastAPI app via ASGI transport."""

    @pytest.mark.anyio
    async def test_execute_tool_via_canonical_http_route(self, client):
        payload = {
            "agent_id": "agent-orch",
            "sender_role": "orchestrator",
            "kwargs": {"query": "hello"},
        }
        resp = await client.post("/tools/web_search/run", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["tool_name"] == "web_search"

    @pytest.mark.anyio
    async def test_execute_tool_via_http(self, client):
        payload = {
            "agent_id": "agent-orch",
            "sender_role": "orchestrator",
            "tool_name": "web_search",
            "kwargs": {"query": "hello"},
        }
        resp = await client.post("/tools/execute", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["tool_name"] == "web_search"

    @pytest.mark.anyio
    async def test_execute_forbidden_via_http(self, client):
        payload = {
            "agent_id": "sub-agent-1",
            "sender_role": "sub_agent",
            "tool_name": "project.create",
            "kwargs": {},
        }
        resp = await client.post("/tools/execute", json=payload)
        assert resp.status_code == 200  # 200 with success=False
        data = resp.json()
        assert data["success"] is False
        assert data["error_code"] == "FORBIDDEN"

    @pytest.mark.anyio
    async def test_execute_unknown_tool_via_http(self, client):
        payload = {
            "agent_id": "agent-1",
            "sender_role": "orchestrator",
            "tool_name": "does.not.exist",
            "kwargs": {},
        }
        resp = await client.post("/tools/execute", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert data["error_code"] == "TOOL_NOT_FOUND"

    @pytest.mark.anyio
    async def test_tools_manifest_via_http(self, client):
        resp = await client.get("/tools")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] >= 30
        names = {t["tool_name"] for t in data["tools"]}
        assert "web_search" in names
        assert "blob.download" in names

    @pytest.mark.anyio
    async def test_health_shows_breaker_states(self, client):
        resp = await client.get("/health")
        data = resp.json()
        assert "circuit_breakers" in data
        assert isinstance(data["circuit_breakers"], list)
        assert len(data["circuit_breakers"]) > 0

    @pytest.mark.anyio
    async def test_response_duration_ms(self, client):
        payload = {
            "agent_id": "agent-1",
            "sender_role": "orchestrator",
            "tool_name": "file_read",
            "kwargs": {"path": "test.txt"},
        }
        resp = await client.post("/tools/execute", json=payload)
        data = resp.json()
        assert data["success"] is True
        assert data["duration_ms"] is not None
        assert data["duration_ms"] >= 0
