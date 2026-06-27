"""Phase 6 tests — tool-service registry, circuit breaker, rate limiter, cache, routes."""

from __future__ import annotations

import anyio
import httpx
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
        for tool_name in [
            "web_search",
            "project.create",
            "infra.provision",
            "sprint.create",
            "flow.recommend",
        ]:
            req = ToolRequest(
                caller_id="ceo-orchestrator",
                caller_role=AgentRole.ORCHESTRATOR,
                tool_name=tool_name,
                tool_kwargs={"project_name": "Build a dashboard"}
                if tool_name == "flow.recommend"
                else {},
            )
            resp = await registry.execute(req)
            assert resp.success is True, (
                f"Orchestrator should be able to call {tool_name}: {resp.error}"
            )

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
    async def test_policy_denial_not_overridden_by_tool_metadata(self, make_registry):
        """Team-scoped policy denials remain authoritative."""
        registry = make_registry()
        req = ToolRequest(
            caller_id="office-cfo",
            caller_role=AgentRole.C_SUITE,
            caller_team="office_cfo",
            tool_name="sprint.create",
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

    @pytest.mark.anyio
    async def test_http_transport_path_is_supported(self, make_registry, monkeypatch):
        from mas_tools_sdk.base import BaseTool
        from mas_tools_sdk.groups import ToolGroup

        class HttpTransportTool(BaseTool):
            name = "capability.register"
            group = ToolGroup.CAPABILITY
            transport = "http"
            allowed_roles = [AgentRole.ORCHESTRATOR]

            async def execute(self, **kwargs):
                return {"should_not": "run"}

        registry = make_registry()
        registry.register(HttpTransportTool())

        async def fake_http(tool_name, kwargs):
            return {"tool": tool_name, "ok": True}

        monkeypatch.setattr(
            registry,
            "_execute_http_transport",
            fake_http,
        )
        req = ToolRequest(
            caller_id="agent-1",
            caller_role=AgentRole.ORCHESTRATOR,
            tool_name="capability.register",
            tool_kwargs={"worker_id": "w1"},
        )
        resp = await registry.execute(req)
        assert resp.success is True
        assert resp.result["ok"] is True

    @pytest.mark.anyio
    async def test_process_transport_path_is_supported(self, make_registry, monkeypatch):
        from mas_tools_sdk.base import BaseTool
        from mas_tools_sdk.groups import ToolGroup

        class ProcessTransportTool(BaseTool):
            name = "capability.deregister"
            group = ToolGroup.CAPABILITY
            transport = "process"
            allowed_roles = [AgentRole.ORCHESTRATOR]

            async def execute(self, **kwargs):
                return {"should_not": "run"}

        registry = make_registry()
        registry.register(ProcessTransportTool())

        async def fake_process(tool_name, kwargs):
            return {"tool": tool_name, "ok": True}

        monkeypatch.setattr(
            registry,
            "_execute_process_transport",
            fake_process,
        )
        req = ToolRequest(
            caller_id="agent-1",
            caller_role=AgentRole.ORCHESTRATOR,
            tool_name="capability.deregister",
            tool_kwargs={"worker_id": "w1"},
        )
        resp = await registry.execute(req)
        assert resp.success is True
        assert resp.result["ok"] is True


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
        from tool_service.rate_limiter import RateLimiterPool

        from mas_tools_sdk.groups import ToolGroup

        pool = RateLimiterPool()
        allowed, remaining, _ = await pool.acquire(ToolGroup.KPI_UTILITY)
        assert allowed is True

    @pytest.mark.anyio
    async def test_get_rate(self):
        from tool_service.rate_limiter import RateLimiterPool

        from mas_tools_sdk.groups import GROUP_RATE_LIMITS, ToolGroup

        pool = RateLimiterPool()
        assert pool.get_rate(ToolGroup.KPI_UTILITY) == GROUP_RATE_LIMITS[ToolGroup.KPI_UTILITY]


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
        assert entry["tool_group"] == "kpi_utility"

    def test_alias_entries_include_deprecation_metadata(self, make_registry):
        registry = make_registry()
        manifest = registry.get_manifest()
        alias = next(e for e in manifest if e["tool_name"] == "document_create")
        assert alias["deprecated_alias_of"] == "document.create_draft"

    def test_project_create_restricted_to_orchestrator(self):
        from mas_tools_sdk.manifest import TOOL_MANIFEST

        entry = TOOL_MANIFEST["project.create"]
        role_values = [r.value if hasattr(r, "value") else r for r in entry["allowed_roles"]]
        assert "orchestrator" in role_values
        assert "worker" not in role_values

    def test_flow_recommend_present_in_manifest(self):
        from mas_tools_sdk.manifest import TOOL_MANIFEST

        assert "flow.recommend" in TOOL_MANIFEST


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
    async def test_execute_legacy_alias_via_http(self, client):
        payload = {
            "agent_id": "agent-orch",
            "sender_role": "orchestrator",
            "tool_name": "document_create",
            "kwargs": {"title": "Doc 1"},
        }
        resp = await client.post("/tools/execute", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["tool_name"] == "document_create"
        assert data["result"]["_canonical_tool"] == "document.create_draft"

    @pytest.mark.anyio
    async def test_capability_lifecycle_via_http(self, client):
        register_payload = {
            "agent_id": "agent-orch",
            "sender_role": "orchestrator",
            "tool_name": "capability.register",
            "kwargs": {"worker_id": "w1", "capabilities": ["implement_feature"]},
        }
        resp = await client.post("/tools/execute", json=register_payload)
        assert resp.status_code == 200
        assert resp.json()["success"] is True

        list_payload = {
            "agent_id": "agent-orch",
            "sender_role": "orchestrator",
            "tool_name": "capability.list_workers",
            "kwargs": {},
        }
        resp = await client.post("/tools/execute", json=list_payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["result"]["count"] >= 1

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
    async def test_project_create_accepts_ceo_title_parameter(self, client, monkeypatch):
        calls: list[dict | None] = []

        async def fake_orch_post(path, body=None):
            calls.append(body)
            return {"id": "project-1", **(body or {})}

        import tool_service.tools.project as project_mod

        monkeypatch.setattr(project_mod, "orch_post", fake_orch_post)

        payload = {
            "agent_id": "ceo-agent",
            "sender_role": "orchestrator",
            "tool_name": "project.create",
            "kwargs": {
                "title": "CEO-created regression project",
                "description": "Created through the CEO tool schema.",
            },
        }
        resp = await client.post("/tools/execute", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["result"]["name"] == "CEO-created regression project"
        assert calls[0]["name"] == "CEO-created regression project"

    @pytest.mark.anyio
    async def test_project_status_invalid_id_returns_tool_error_without_orchestrator_call(
        self, client, monkeypatch
    ):
        calls: list[str] = []

        async def fake_orch_get(path, params=None):
            calls.append(path)
            return {"id": "should-not-be-called"}

        import tool_service.tools.project as project_mod

        monkeypatch.setattr(project_mod, "orch_get", fake_orch_get)

        payload = {
            "agent_id": "ceo-agent",
            "sender_role": "orchestrator",
            "tool_name": "project.status",
            "kwargs": {"project_id": "operator-direct"},
        }
        resp = await client.post("/tools/execute", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["result"] == {
            "error": "invalid_project_id",
            "project_id": "operator-direct",
        }
        assert calls == []

    @pytest.mark.anyio
    async def test_project_status_missing_project_returns_tool_error_without_opening_breaker(
        self, client, monkeypatch
    ):
        missing_id = "90e1b71f-7ff3-4ff6-8072-72ea6c600988"

        async def fake_orch_get(path, params=None):
            request = httpx.Request("GET", f"http://orchestrator-api:8000{path}")
            response = httpx.Response(404, request=request)
            raise httpx.HTTPStatusError("not found", request=request, response=response)

        import tool_service.tools.project as project_mod

        monkeypatch.setattr(project_mod, "orch_get", fake_orch_get)

        payload = {
            "agent_id": "ceo-agent",
            "sender_role": "orchestrator",
            "tool_name": "project.status",
            "kwargs": {"project_id": missing_id},
        }
        resp = await client.post("/tools/execute", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["result"] == {
            "error": "project_not_found",
            "project_id": missing_id,
        }

    @pytest.mark.anyio
    async def test_human_notify_publishes_ceo_response_envelope(self, client, monkeypatch):
        published: list[dict] = []

        async def fake_publish_message(envelope):
            published.append(envelope)
            return {"entry_id": "stream-entry-1"}

        import tool_service.tools.project as project_mod

        monkeypatch.setattr(project_mod, "publish_message", fake_publish_message)

        payload = {
            "agent_id": "ceo-agent",
            "sender_role": "orchestrator",
            "tool_name": "human.notify",
            "kwargs": {
                "project_id": "operator-direct",
                "message": "The feasibility report is ready.",
                "notification_type": "INFO",
            },
        }
        resp = await client.post("/tools/execute", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["result"] == {
            "notified": True,
            "entry_id": "stream-entry-1",
            "project_id": "operator-direct",
            "message": "The feasibility report is ready.",
            "notification_type": "INFO",
        }
        assert len(published) == 1
        envelope = published[0]
        assert envelope["msg_type"] == "RESPONSE"
        assert envelope["sender_id"] == "ceo"
        assert envelope["sender_role"] == "orchestrator"
        assert envelope["sender_team"] == "exec_ceo"
        assert envelope["recipient_team"] == "exec_ceo"
        assert envelope["project_id"] == "operator-direct"
        assert envelope["payload"] == {
            "response": "The feasibility report is ready.",
            "source": "human.notify",
            "notification_type": "INFO",
        }

    @pytest.mark.anyio
    async def test_human_await_decision_returns_full_pending_decision_payload(
        self, client, monkeypatch
    ):
        async def fake_orch_get(path, params=None):
            assert path == "/projects/project-1/pending-decisions"
            return [
                {
                    "id": "gate-1",
                    "gate_type": "human",
                    "title": "Approve launch?",
                    "description": "Operator approval required before rollout.",
                    "options": ["approved", "edit_requested", "rejected"],
                },
                {
                    "id": "gate-2",
                    "gate_type": "security",
                    "title": "Approve privileged action?",
                },
            ]

        import tool_service.tools.project as project_mod

        monkeypatch.setattr(project_mod, "orch_get", fake_orch_get)

        resp = await client.post(
            "/tools/execute",
            json={
                "agent_id": "ceo-agent",
                "sender_role": "orchestrator",
                "tool_name": "human.await_decision",
                "kwargs": {"project_id": "project-1"},
            },
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        result = data["result"]
        assert result["pending"] is True
        assert result["pending_count"] == 2
        assert result["gate_id"] == "gate-1"
        assert result["gate_type"] == "human"
        assert result["first_decision"]["title"] == "Approve launch?"
        assert result["decisions"][1]["title"] == "Approve privileged action?"

    @pytest.mark.anyio
    async def test_human_await_decision_returns_stable_empty_shape(self, client, monkeypatch):
        async def fake_orch_get(path, params=None):
            assert path == "/projects/project-1/pending-decisions"
            return []

        import tool_service.tools.project as project_mod

        monkeypatch.setattr(project_mod, "orch_get", fake_orch_get)

        resp = await client.post(
            "/tools/execute",
            json={
                "agent_id": "ceo-agent",
                "sender_role": "orchestrator",
                "tool_name": "human.await_decision",
                "kwargs": {"project_id": "project-1"},
            },
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["result"] == {
            "pending": False,
            "pending_count": 0,
            "decisions": [],
            "message": "No pending decisions",
        }

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
    async def test_flow_recommend_selects_software_build_flow(self, client, monkeypatch):
        async def fake_orch_get(path, params=None):
            if path == "/flows":
                return [
                    {
                        "id": "flow-software",
                        "name": "Software Build Flow",
                        "version": 1,
                        "description": "Build software dashboards",
                    },
                    {
                        "id": "flow-research",
                        "name": "Research Flow",
                        "version": 1,
                        "description": "Research and discovery",
                    },
                ]
            return {"id": "project-1"}

        import tool_service.tools.flow as flow_mod

        monkeypatch.setattr(flow_mod, "orch_get", fake_orch_get)

        payload = {
            "agent_id": "ceo-agent",
            "sender_role": "orchestrator",
            "tool_name": "flow.recommend",
            "kwargs": {
                "project_name": "Build a dashboard",
                "project_description": "software implementation",
            },
        }
        resp = await client.post("/tools/execute", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["result"]["selected_flow_name"] == "Software Build Flow"

    @pytest.mark.anyio
    async def test_flow_assign_can_create_and_start_project_flow(self, client, monkeypatch):
        calls: list[tuple[str, dict | None]] = []

        async def fake_orch_get(path, params=None):
            if path == "/projects/project-1/flow-instance":
                return {"status": 404}
            return {}

        async def fake_orch_post(path, body=None):
            calls.append((path, body))
            if path == "/flows/instances":
                return {"id": "instance-1", "flow_id": "flow-software", "status": "NOT_STARTED"}
            if path == "/flows/instances/instance-1/action":
                return {
                    "id": "instance-1",
                    "flow_id": "flow-software",
                    "status": "RUNNING",
                    "active_node_ids": ["start"],
                }
            return {"id": "instance-1"}

        import tool_service.tools.flow as flow_mod

        monkeypatch.setattr(flow_mod, "orch_get", fake_orch_get)
        monkeypatch.setattr(flow_mod, "orch_post", fake_orch_post)

        payload = {
            "agent_id": "ceo-agent",
            "sender_role": "orchestrator",
            "tool_name": "flow.assign",
            "kwargs": {
                "project_id": "project-1",
                "flow_id": "flow-software",
                "start_after_assign": True,
            },
        }
        resp = await client.post("/tools/execute", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["result"]["action"] == "created_and_started"
        assert calls == [
            ("/flows/instances", {"flow_id": "flow-software", "project_id": "project-1"}),
            ("/flows/instances/instance-1/action", {"action": "start"}),
        ]

    @pytest.mark.anyio
    async def test_flow_assign_can_switch_existing_flow(self, client, monkeypatch):
        calls: list[tuple[str, dict | None]] = []

        async def fake_orch_get(path, params=None):
            if path == "/projects/project-1/flow-instance":
                return {"id": "instance-1", "flow_id": "flow-software", "status": "RUNNING"}
            return {}

        async def fake_orch_post(path, body=None):
            calls.append((path, body))
            return {"id": "instance-1", "flow_id": "flow-research", "status": "NOT_STARTED"}

        import tool_service.tools.flow as flow_mod

        monkeypatch.setattr(flow_mod, "orch_get", fake_orch_get)
        monkeypatch.setattr(flow_mod, "orch_post", fake_orch_post)

        payload = {
            "agent_id": "human-operator",
            "sender_role": "orchestrator",
            "tool_name": "flow.assign",
            "kwargs": {"project_id": "project-1", "flow_id": "flow-research"},
        }
        resp = await client.post("/tools/execute", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["result"]["action"] == "switched"
        assert calls == [
            ("/flows/instances/instance-1/switch", {"flow_id": "flow-research"}),
        ]

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
