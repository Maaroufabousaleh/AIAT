"""
Test 6 — Centralized Tool Layer (advanced-integration)
=======================================================

Validates the end-to-end operator flow:
1.  Tool registration (web_search, browser_navigate, document.create_draft,
    document.get_latest, blob.upload, blob.download)
2.  Per-worker tool grant assignment & denial
3.  Verified tool call through centralized service (not direct worker call)
4.  Audit log: actor, project_id, tool_name, timestamp, status, error
5.  Rate-limit exhaustion → RATE_LIMITED response
6.  Circuit-breaker: 3 failures → OPEN → HALF_OPEN → success → CLOSED

Test types: unit, API/integration, security, negative-case.
Fixtures:   make_registry (ToolRegistry with no Redis), client (ASGI httpx).
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import anyio
import pytest

from mas_core.protocols.enums import AgentRole
from mas_core.protocols.tool import CircuitState, ToolRequest
from mas_tools_sdk.base import BaseTool
from mas_tools_sdk.groups import ToolGroup

# ---------------------------------------------------------------------------
# Fake browser tool (no playwright dependency)
# ---------------------------------------------------------------------------


class FakeBrowserTool(BaseTool):
    """Stub browser_navigate tool for tests — never needs playwright."""

    name = "browser_navigate"
    group = ToolGroup.KPI_UTILITY
    idempotent = False
    cache_ttl_seconds = 0
    max_concurrency = 0

    async def execute(self, **kwargs) -> dict:  # type: ignore[override]
        return {"navigated": True, "url": kwargs.get("url", "https://example.com")}


# ---------------------------------------------------------------------------
# Fixtures (module-level so they are available to all test classes)
# ---------------------------------------------------------------------------


@pytest.fixture
def make_registry_with_browser(make_registry):
    """Like make_registry but always includes FakeBrowserTool in the registry."""

    def _factory(*, cache=None):
        registry = make_registry(cache=cache)
        # Always replace browser_navigate with the fake tool so these tests
        # validate central policy/circuit behavior without real browser I/O.
        registry.register(FakeBrowserTool())
        return registry

    return _factory


@pytest.fixture
async def client_with_browser():
    """ASGI httpx client with FakeBrowserTool injected into the app registry."""
    pytest.importorskip("fastapi")
    import httpx as _httpx
    from tool_service.main import app
    from tool_service.tools.all_tools import get_all_tools as _get_all_tools

    def _patched_get_all_tools():
        tools = _get_all_tools()
        tools = [tool for tool in tools if tool.name != "browser_navigate"]
        tools.append(FakeBrowserTool())
        return tools

    with patch("tool_service.main.get_all_tools", _patched_get_all_tools):
        async with (
            app.router.lifespan_context(app),
            _httpx.AsyncClient(
                transport=_httpx.ASGITransport(app=app, raise_app_exceptions=False),
                base_url="http://test",
            ) as ac,
        ):
            yield ac


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

WORKER_ID = "worker-alpha"
PROJECT_ID = "proj-test6"

SEARCH_TOOL = "web_search"
BROWSER_TOOL = "browser_navigate"
DOC_CREATE_TOOL = "document.create_draft"
DOC_GET_TOOL = "document.get_latest"
BLOB_UPLOAD_TOOL = "blob.upload"
BLOB_DOWNLOAD_TOOL = "blob.download"

ALL_TEST_TOOLS = [
    SEARCH_TOOL,
    BROWSER_TOOL,
    DOC_CREATE_TOOL,
    DOC_GET_TOOL,
    BLOB_UPLOAD_TOOL,
    BLOB_DOWNLOAD_TOOL,
]


def _req(
    tool_name: str,
    *,
    caller_id: str = WORKER_ID,
    role: AgentRole = AgentRole.WORKER,
    project_id: str | None = PROJECT_ID,
    kwargs: dict | None = None,
) -> ToolRequest:
    return ToolRequest(
        caller_id=caller_id,
        caller_role=role,
        tool_name=tool_name,
        project_id=project_id,
        tool_kwargs=kwargs or {},
    )


# ---------------------------------------------------------------------------
# Section 1 — Tool registration (manifest & registry)
# ---------------------------------------------------------------------------


class TestToolRegistration:
    """All six test-6 tools are registered and manifest-visible."""

    def test_all_six_tools_in_manifest(self):
        from mas_tools_sdk.manifest import TOOL_MANIFEST

        for name in ALL_TEST_TOOLS:
            assert name in TOOL_MANIFEST, f"Tool '{name}' missing from manifest"

    def test_all_six_tools_registered_in_registry(self, make_registry_with_browser):
        registry = make_registry_with_browser()
        names = set(registry.tool_names)
        for name in ALL_TEST_TOOLS:
            assert name in names, f"Tool '{name}' not registered in ToolRegistry"

    def test_manifest_entries_have_required_fields(self):
        from mas_tools_sdk.manifest import TOOL_MANIFEST

        required = {"tool_name", "tool_group", "description", "allowed_roles"}
        for name in ALL_TEST_TOOLS:
            entry = TOOL_MANIFEST[name]
            missing = required - set(entry.keys())
            assert not missing, f"Tool '{name}' manifest entry missing fields: {missing}"

    def test_browser_tool_allows_worker_role(self):
        """browser_navigate is accessible to WORKER role by manifest."""
        from mas_tools_sdk.manifest import TOOL_MANIFEST

        entry = TOOL_MANIFEST[BROWSER_TOOL]
        role_values = [r.value if hasattr(r, "value") else r for r in entry["allowed_roles"]]
        assert "worker" in role_values

    def test_web_search_allows_worker_role(self):
        from mas_tools_sdk.manifest import TOOL_MANIFEST

        entry = TOOL_MANIFEST[SEARCH_TOOL]
        role_values = [r.value if hasattr(r, "value") else r for r in entry["allowed_roles"]]
        assert "worker" in role_values

    @pytest.mark.anyio
    async def test_list_tools_http_includes_all_six(self, client):
        resp = await client.get("/tools")
        assert resp.status_code == 200
        names = {t["tool_name"] for t in resp.json()["tools"]}
        for name in ALL_TEST_TOOLS:
            assert name in names, f"Tool '{name}' missing from GET /tools response"


# ---------------------------------------------------------------------------
# Section 2 — Per-worker tool grant: unit (ToolRegistry directly)
# ---------------------------------------------------------------------------


class TestWorkerGrantUnit:
    """Unit tests for ToolRegistry.grant_tool / revoke_tool / get_worker_grants."""

    def test_no_grants_gate_is_open(self, make_registry):
        registry = make_registry()
        # Worker with no explicit grants passes the gate for any tool
        assert registry._check_worker_grant(WORKER_ID, SEARCH_TOOL) is True
        assert registry._check_worker_grant(WORKER_ID, BROWSER_TOOL) is True

    def test_grant_allows_tool(self, make_registry):
        registry = make_registry()
        registry.grant_tool(WORKER_ID, SEARCH_TOOL)
        assert registry._check_worker_grant(WORKER_ID, SEARCH_TOOL) is True

    def test_grant_denies_ungrated_tool(self, make_registry):
        registry = make_registry()
        registry.grant_tool(WORKER_ID, SEARCH_TOOL)
        # browser not granted → denied
        assert registry._check_worker_grant(WORKER_ID, BROWSER_TOOL) is False

    def test_get_worker_grants_returns_sorted_list(self, make_registry):
        registry = make_registry()
        registry.grant_tool(WORKER_ID, SEARCH_TOOL)
        registry.grant_tool(WORKER_ID, BLOB_UPLOAD_TOOL)
        grants = registry.get_worker_grants(WORKER_ID)
        assert SEARCH_TOOL in grants
        assert BLOB_UPLOAD_TOOL in grants
        assert grants == sorted(grants)

    def test_revoke_removes_grant(self, make_registry):
        registry = make_registry()
        registry.grant_tool(WORKER_ID, SEARCH_TOOL)
        removed = registry.revoke_tool(WORKER_ID, SEARCH_TOOL)
        assert removed is True
        # After revoke the gate is based on what remains in the set.
        # If set is now empty, the worker still has the explicit grant dict
        # but the tool is not in it — so gate is closed.
        assert registry._check_worker_grant(WORKER_ID, SEARCH_TOOL) is False

    def test_revoke_nonexistent_returns_false(self, make_registry):
        registry = make_registry()
        assert registry.revoke_tool(WORKER_ID, SEARCH_TOOL) is False

    def test_multiple_workers_independent(self, make_registry):
        registry = make_registry()
        registry.grant_tool("worker-A", SEARCH_TOOL)
        registry.grant_tool("worker-B", BROWSER_TOOL)
        assert registry._check_worker_grant("worker-A", SEARCH_TOOL) is True
        assert registry._check_worker_grant("worker-A", BROWSER_TOOL) is False
        assert registry._check_worker_grant("worker-B", BROWSER_TOOL) is True
        assert registry._check_worker_grant("worker-B", SEARCH_TOOL) is False


# ---------------------------------------------------------------------------
# Section 3 — Per-worker tool grant: integration (registry.execute)
# ---------------------------------------------------------------------------


class TestWorkerGrantIntegration:
    """Worker with explicit grants can only call granted tools (policy gate)."""

    @pytest.mark.anyio
    async def test_worker_with_grant_can_call_web_search(self, make_registry):
        registry = make_registry()
        registry.grant_tool(WORKER_ID, SEARCH_TOOL)
        resp = await registry.execute(_req(SEARCH_TOOL))
        assert resp.success is True
        assert resp.tool_name == SEARCH_TOOL

    @pytest.mark.anyio
    async def test_worker_denied_browser_when_not_granted(self, make_registry_with_browser):
        """Even though WORKER role allows browser_navigate, a worker with an
        explicit grant list must have browser_navigate in that list."""
        registry = make_registry_with_browser()
        # Grant only web_search → browser not in grant list
        registry.grant_tool(WORKER_ID, SEARCH_TOOL)
        resp = await registry.execute(_req(BROWSER_TOOL))
        assert resp.success is False
        assert resp.error_code == "FORBIDDEN"
        assert WORKER_ID in resp.error

    @pytest.mark.anyio
    async def test_worker_allowed_browser_after_grant(self, make_registry_with_browser):
        """After granting browser, the same worker can call it."""
        registry = make_registry_with_browser()
        registry.grant_tool(WORKER_ID, SEARCH_TOOL)
        registry.grant_tool(WORKER_ID, BROWSER_TOOL)
        resp = await registry.execute(_req(BROWSER_TOOL, kwargs={"url": "https://example.com"}))
        assert resp.success is True

    @pytest.mark.anyio
    async def test_worker_without_any_grants_uses_role_policy_only(self, make_registry):
        """A worker with NO explicit grants is governed by role policy alone."""
        registry = make_registry()
        # web_search is WORKER-allowed by role — no grants set → should pass
        resp = await registry.execute(_req(SEARCH_TOOL, caller_id="clean-worker"))
        assert resp.success is True

    @pytest.mark.anyio
    async def test_grant_list_empty_after_revoke_blocks_all(self, make_registry):
        """After revoking the only grant the worker's grant set is empty,
        meaning ALL tools are blocked (worker has explicit grants dict but no entries)."""
        registry = make_registry()
        registry.grant_tool(WORKER_ID, SEARCH_TOOL)
        registry.revoke_tool(WORKER_ID, SEARCH_TOOL)
        resp = await registry.execute(_req(SEARCH_TOOL))
        assert resp.success is False
        assert resp.error_code == "FORBIDDEN"

    @pytest.mark.anyio
    async def test_role_policy_still_blocks_forbidden_roles(self, make_registry):
        """Role-based block applies even without per-worker grants."""
        registry = make_registry()
        # SUB_AGENT cannot call document.create_draft (EXECUTIVE/ORCHESTRATOR only)
        resp = await registry.execute(
            _req(DOC_CREATE_TOOL, caller_id="sa-1", role=AgentRole.SUB_AGENT)
        )
        assert resp.success is False
        assert resp.error_code == "FORBIDDEN"


# ---------------------------------------------------------------------------
# Section 4 — Per-worker grant HTTP API (ASGI client)
# ---------------------------------------------------------------------------


class TestWorkerGrantHTTP:
    """Operator-style: grant/revoke/list tool assignments through public API."""

    @pytest.mark.anyio
    async def test_grant_tool_creates_entry(self, client):
        resp = await client.post(
            f"/tools/workers/{WORKER_ID}/grants",
            json={"tool_name": SEARCH_TOOL},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["worker_id"] == WORKER_ID
        assert SEARCH_TOOL in data["grants"]

    @pytest.mark.anyio
    async def test_get_grants_shows_assigned_tools(self, client):
        await client.post(
            f"/tools/workers/w-list/grants",
            json={"tool_name": SEARCH_TOOL},
        )
        await client.post(
            f"/tools/workers/w-list/grants",
            json={"tool_name": BLOB_DOWNLOAD_TOOL},
        )
        resp = await client.get("/tools/workers/w-list/grants")
        assert resp.status_code == 200
        data = resp.json()
        assert SEARCH_TOOL in data["grants"]
        assert BLOB_DOWNLOAD_TOOL in data["grants"]

    @pytest.mark.anyio
    async def test_worker_denied_before_grant_via_http(self, client_with_browser):
        worker = "w-pre-grant"
        # Give worker an explicit list with only web_search
        await client_with_browser.post(
            f"/tools/workers/{worker}/grants",
            json={"tool_name": SEARCH_TOOL},
        )
        # Execute browser tool — should be denied
        payload = {
            "agent_id": worker,
            "sender_role": "worker",
            "kwargs": {"url": "https://example.com"},
            "project_id": PROJECT_ID,
        }
        resp = await client_with_browser.post(f"/tools/{BROWSER_TOOL}/run", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert data["error_code"] == "FORBIDDEN"
        assert worker in data["error"]

    @pytest.mark.anyio
    async def test_worker_allowed_after_grant_via_http(self, client_with_browser):
        worker = "w-post-grant"
        # Step 1: grant only web_search
        await client_with_browser.post(
            f"/tools/workers/{worker}/grants",
            json={"tool_name": SEARCH_TOOL},
        )
        # Step 2: grant browser
        g_resp = await client_with_browser.post(
            f"/tools/workers/{worker}/grants",
            json={"tool_name": BROWSER_TOOL},
        )
        assert g_resp.status_code == 201
        assert BROWSER_TOOL in g_resp.json()["grants"]

        # Step 3: retry browser — should succeed now
        payload = {
            "agent_id": worker,
            "sender_role": "worker",
            "kwargs": {"url": "https://example.com"},
            "project_id": PROJECT_ID,
        }
        resp = await client_with_browser.post(f"/tools/{BROWSER_TOOL}/run", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True

    @pytest.mark.anyio
    async def test_revoke_tool_removes_access(self, client_with_browser):
        worker = "w-revoke"
        # Grant both tools
        await client_with_browser.post(
            f"/tools/workers/{worker}/grants", json={"tool_name": SEARCH_TOOL}
        )
        await client_with_browser.post(
            f"/tools/workers/{worker}/grants", json={"tool_name": BROWSER_TOOL}
        )
        # Revoke browser
        del_resp = await client_with_browser.delete(
            f"/tools/workers/{worker}/grants/{BROWSER_TOOL}"
        )
        assert del_resp.status_code == 200
        assert BROWSER_TOOL not in del_resp.json()["grants"]
        # Now browser is denied again
        payload = {
            "agent_id": worker,
            "sender_role": "worker",
            "kwargs": {"url": "https://example.com"},
        }
        resp = await client_with_browser.post(f"/tools/{BROWSER_TOOL}/run", json=payload)
        data = resp.json()
        assert data["success"] is False
        assert data["error_code"] == "FORBIDDEN"

    @pytest.mark.anyio
    async def test_revoke_nonexistent_grant_returns_404(self, client):
        resp = await client.delete(f"/tools/workers/nobody/grants/{SEARCH_TOOL}")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Section 5 — Audit log
# ---------------------------------------------------------------------------


class TestAuditLog:
    """Every tool call (success or failure) is recorded with required fields."""

    REQUIRED_FIELDS = {"actor", "project_id", "tool_name", "timestamp", "status"}

    @pytest.mark.anyio
    async def test_success_call_recorded_in_audit(self, make_registry):
        registry = make_registry()
        await registry.execute(_req(SEARCH_TOOL, caller_id="audit-w1"))
        logs = registry.get_audit_log(worker_id="audit-w1")
        assert len(logs) == 1
        rec = logs[0]
        assert self.REQUIRED_FIELDS.issubset(rec.keys())
        assert rec["actor"] == "audit-w1"
        assert rec["tool_name"] == SEARCH_TOOL
        assert rec["project_id"] == PROJECT_ID
        assert rec["status"] == "success"
        assert rec["error"] is None
        assert rec["timestamp"]  # non-empty ISO string

    @pytest.mark.anyio
    async def test_forbidden_call_recorded_in_audit(self, make_registry_with_browser):
        registry = make_registry_with_browser()
        registry.grant_tool("audit-w2", SEARCH_TOOL)  # only search granted
        await registry.execute(_req(BROWSER_TOOL, caller_id="audit-w2"))
        logs = registry.get_audit_log(worker_id="audit-w2")
        assert any(r["status"] == "forbidden" for r in logs)
        rec = next(r for r in logs if r["status"] == "forbidden")
        assert rec["tool_name"] == BROWSER_TOOL
        assert rec["error"] is not None

    @pytest.mark.anyio
    async def test_error_call_recorded_in_audit(self, make_registry_with_browser):
        registry = make_registry_with_browser()
        # Force browser tool execute to raise
        browser_tool = registry._tools[BROWSER_TOOL]
        orig = browser_tool.execute

        async def boom(**kwargs):
            raise RuntimeError("playwright unavailable")

        browser_tool.execute = boom
        try:
            await registry.execute(
                _req(BROWSER_TOOL, caller_id="audit-w3", kwargs={"url": "https://example.com"})
            )
        finally:
            browser_tool.execute = orig

        logs = registry.get_audit_log(worker_id="audit-w3")
        assert any(r["status"] == "error" for r in logs)
        rec = next(r for r in logs if r["status"] == "error")
        assert rec["error"] is not None
        assert "playwright" in rec["error"].lower() or "unavailable" in rec["error"].lower()

    @pytest.mark.anyio
    async def test_audit_filter_by_worker_id(self, make_registry):
        registry = make_registry()
        await registry.execute(_req(SEARCH_TOOL, caller_id="filter-w1"))
        await registry.execute(_req(SEARCH_TOOL, caller_id="filter-w2"))
        logs_w1 = registry.get_audit_log(worker_id="filter-w1")
        assert all(r["actor"] == "filter-w1" for r in logs_w1)
        logs_w2 = registry.get_audit_log(worker_id="filter-w2")
        assert all(r["actor"] == "filter-w2" for r in logs_w2)

    @pytest.mark.anyio
    async def test_audit_filter_by_tool_name(self, make_registry):
        registry = make_registry()
        await registry.execute(_req(SEARCH_TOOL, caller_id="ft-worker"))
        await registry.execute(
            _req(
                BLOB_DOWNLOAD_TOOL,
                caller_id="ft-worker",
                role=AgentRole.WORKER,
                kwargs={"bucket": "b", "key": "k"},
            )
        )
        logs = registry.get_audit_log(tool_name=SEARCH_TOOL)
        assert all(r["tool_name"] == SEARCH_TOOL for r in logs)

    @pytest.mark.anyio
    async def test_audit_http_endpoint_returns_records(self, client):
        worker = "audit-http-w1"
        payload = {
            "agent_id": worker,
            "sender_role": "worker",
            "kwargs": {"query": "hello"},
            "project_id": PROJECT_ID,
        }
        await client.post("/tools/execute", json={**payload, "tool_name": SEARCH_TOOL})
        resp = await client.get(f"/tools/audit?worker_id={worker}")
        assert resp.status_code == 200
        data = resp.json()
        assert "records" in data
        assert data["count"] >= 1
        rec = data["records"][0]
        required = {"actor", "project_id", "tool_name", "timestamp", "status"}
        assert required.issubset(rec.keys())
        assert rec["actor"] == worker
        assert rec["tool_name"] == SEARCH_TOOL

    @pytest.mark.anyio
    async def test_audit_http_filter_by_tool(self, client):
        worker = "audit-http-w2"
        for tool in [SEARCH_TOOL, BLOB_DOWNLOAD_TOOL]:
            payload = {
                "agent_id": worker,
                "sender_role": "worker",
                "tool_name": tool,
                "kwargs": {"query": "x"} if tool == SEARCH_TOOL else {"bucket": "b", "key": "k"},
            }
            await client.post("/tools/execute", json=payload)
        resp = await client.get(f"/tools/audit?tool_name={SEARCH_TOOL}")
        assert resp.status_code == 200
        records = resp.json()["records"]
        assert all(r["tool_name"] == SEARCH_TOOL for r in records)

    @pytest.mark.anyio
    async def test_audit_record_has_duration_ms_on_success(self, make_registry):
        registry = make_registry()
        await registry.execute(_req(SEARCH_TOOL, caller_id="dur-worker"))
        logs = registry.get_audit_log(worker_id="dur-worker")
        assert logs[0]["duration_ms"] is not None
        assert logs[0]["duration_ms"] >= 0


# ---------------------------------------------------------------------------
# Section 6 — Rate limiting
# ---------------------------------------------------------------------------


class TestRateLimiting:
    """Exceeding rate limit returns RATE_LIMITED with correct metadata."""

    @pytest.mark.anyio
    async def test_rate_limit_exhaustion_returns_rate_limited(self, make_registry):
        from tool_service.rate_limiter import RateLimiterPool
        from tool_service.config import Settings
        from tool_service.registry import ToolRegistry
        from tool_service.tools.all_tools import get_all_tools
        from mas_tools_sdk.groups import ToolGroup

        # Create a registry with a 1-call-per-minute cap on KPI_UTILITY (web_search group)
        settings = Settings()
        rate_limiter = RateLimiterPool(overrides={ToolGroup.KPI_UTILITY: 1})
        registry = ToolRegistry(settings, cache=None, rate_limiter=rate_limiter)
        registry.register_all(get_all_tools())

        req = _req(SEARCH_TOOL, caller_id="rl-worker")
        # First call succeeds (consumes the 1 token)
        resp1 = await registry.execute(req)
        assert resp1.success is True

        # Second call is rate-limited
        resp2 = await registry.execute(req)
        assert resp2.success is False
        assert resp2.error_code == "RATE_LIMITED"
        assert resp2.rate_limit_remaining == 0

    @pytest.mark.anyio
    async def test_rate_limited_response_via_http_returns_429(self, client, monkeypatch):
        """When rate limit is exceeded the HTTP layer wraps the response in 429."""
        from tool_service.rate_limiter import RateLimiterPool
        from mas_tools_sdk.groups import ToolGroup

        # Exhaust the rate limiter by patching acquire to always deny
        async def deny_acquire(group):
            from datetime import UTC, datetime

            return False, 0, datetime.now(tz=UTC)

        # Patch the registry rate limiter on the app state after client is set up
        registry = client._transport.app.state.registry  # type: ignore[attr-defined]
        monkeypatch.setattr(registry._rate_limiter, "acquire", deny_acquire)

        payload = {
            "agent_id": "rl-http-worker",
            "sender_role": "worker",
            "tool_name": SEARCH_TOOL,
            "kwargs": {"query": "test"},
        }
        resp = await client.post("/tools/execute", json=payload)
        assert resp.status_code == 429
        data = resp.json()
        assert data["error_code"] == "RATE_LIMITED"

    @pytest.mark.anyio
    async def test_rate_limit_audit_record_status(self, make_registry):
        from tool_service.rate_limiter import RateLimiterPool
        from tool_service.config import Settings
        from tool_service.registry import ToolRegistry
        from tool_service.tools.all_tools import get_all_tools
        from mas_tools_sdk.groups import ToolGroup

        settings = Settings()
        rate_limiter = RateLimiterPool(overrides={ToolGroup.KPI_UTILITY: 1})
        registry = ToolRegistry(settings, cache=None, rate_limiter=rate_limiter)
        registry.register_all(get_all_tools())

        await registry.execute(_req(SEARCH_TOOL, caller_id="rl-audit"))
        await registry.execute(_req(SEARCH_TOOL, caller_id="rl-audit"))

        logs = registry.get_audit_log(worker_id="rl-audit")
        statuses = [r["status"] for r in logs]
        assert "rate_limited" in statuses


# ---------------------------------------------------------------------------
# Section 7 — Circuit breaker full lifecycle
# ---------------------------------------------------------------------------


class TestCircuitBreakerLifecycle:
    """Force 3 failures → OPEN → HALF_OPEN probe → success → CLOSED."""

    @pytest.mark.anyio
    async def test_three_failures_open_circuit(self, make_registry_with_browser):
        registry = make_registry_with_browser()
        call_count = 0

        async def failing_execute(self, **kwargs):
            nonlocal call_count
            call_count += 1
            raise RuntimeError("simulated browser failure")

        orig = FakeBrowserTool.execute
        FakeBrowserTool.execute = failing_execute
        try:
            for _ in range(3):
                resp = await registry.execute(
                    _req(BROWSER_TOOL, caller_id="cb-worker", kwargs={"url": "https://example.com"})
                )
                assert resp.success is False
                assert resp.error_code == "TOOL_ERROR"

            # Now circuit should be OPEN
            assert registry._breakers[BROWSER_TOOL].state == CircuitState.OPEN

            # Next call returns CIRCUIT_OPEN immediately
            resp = await registry.execute(
                _req(BROWSER_TOOL, caller_id="cb-worker", kwargs={"url": "https://example.com"})
            )
            assert resp.success is False
            assert resp.error_code == "CIRCUIT_OPEN"
        finally:
            FakeBrowserTool.execute = orig

    @pytest.mark.anyio
    async def test_circuit_transitions_half_open_after_cooldown(self):
        from tool_service.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker("test_cb", failure_threshold=1, open_duration=0.02)
        await cb.record_failure()
        assert cb.state == CircuitState.OPEN
        await anyio.sleep(0.03)
        assert cb.state == CircuitState.HALF_OPEN
        assert await cb.allow_request() is True

    @pytest.mark.anyio
    async def test_half_open_success_closes_circuit(self):
        from tool_service.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker("test_cb", failure_threshold=1, open_duration=0.02)
        await cb.record_failure()
        await anyio.sleep(0.03)
        assert cb.state == CircuitState.HALF_OPEN
        await cb.record_success()
        assert cb.state == CircuitState.CLOSED

    @pytest.mark.anyio
    async def test_half_open_probe_failure_reopens(self):
        from tool_service.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker("test_cb", failure_threshold=1, open_duration=0.02)
        await cb.record_failure()
        await anyio.sleep(0.03)
        assert cb.state == CircuitState.HALF_OPEN
        await cb.record_failure()
        assert cb.state == CircuitState.OPEN

    @pytest.mark.anyio
    async def test_full_circuit_lifecycle_through_registry(self, make_registry_with_browser):
        """Integration: failures → OPEN → wait → HALF_OPEN → success → CLOSED."""
        from tool_service.circuit_breaker import CircuitBreaker

        registry = make_registry_with_browser()
        # Override breaker to have very short cooldown
        registry._breakers[BROWSER_TOOL] = CircuitBreaker(
            BROWSER_TOOL, failure_threshold=3, failure_window=60.0, open_duration=0.05
        )

        fail_mode = {"active": True}

        async def conditional_execute(self, **kwargs):
            if fail_mode["active"]:
                raise RuntimeError("browser down")
            return {"navigated": True}

        orig = FakeBrowserTool.execute
        FakeBrowserTool.execute = conditional_execute
        try:
            # 1. Three failures → OPEN
            for _ in range(3):
                await registry.execute(
                    _req(BROWSER_TOOL, caller_id="cb-full", kwargs={"url": "https://a.com"})
                )
            assert registry._breakers[BROWSER_TOOL].state == CircuitState.OPEN

            # 2. Verify CIRCUIT_OPEN is returned and recorded in audit
            resp = await registry.execute(
                _req(BROWSER_TOOL, caller_id="cb-full", kwargs={"url": "https://a.com"})
            )
            assert resp.error_code == "CIRCUIT_OPEN"
            logs = registry.get_audit_log(worker_id="cb-full")
            assert any(r["status"] == "circuit_open" for r in logs)

            # 3. Wait for cooldown → HALF_OPEN
            await anyio.sleep(0.06)
            assert registry._breakers[BROWSER_TOOL].state == CircuitState.HALF_OPEN

            # 4. Fix tool and send probe call
            fail_mode["active"] = False
            resp = await registry.execute(
                _req(BROWSER_TOOL, caller_id="cb-full", kwargs={"url": "https://a.com"})
            )
            assert resp.success is True

            # 5. Circuit is CLOSED again
            assert registry._breakers[BROWSER_TOOL].state == CircuitState.CLOSED
        finally:
            FakeBrowserTool.execute = orig

    @pytest.mark.anyio
    async def test_circuit_open_audit_recorded(self, make_registry_with_browser):
        from tool_service.circuit_breaker import CircuitBreaker

        registry = make_registry_with_browser()
        registry._breakers[BROWSER_TOOL] = CircuitBreaker(
            BROWSER_TOOL, failure_threshold=2, failure_window=60.0, open_duration=60.0
        )

        async def fail_execute(self, **kwargs):
            raise RuntimeError("cb-audit failure")

        orig = FakeBrowserTool.execute
        FakeBrowserTool.execute = fail_execute
        try:
            for _ in range(2):
                await registry.execute(
                    _req(BROWSER_TOOL, caller_id="cb-audit-w", kwargs={"url": "https://b.com"})
                )
            # Circuit is now OPEN
            await registry.execute(
                _req(BROWSER_TOOL, caller_id="cb-audit-w", kwargs={"url": "https://b.com"})
            )
        finally:
            FakeBrowserTool.execute = orig

        logs = registry.get_audit_log(worker_id="cb-audit-w")
        statuses = [r["status"] for r in logs]
        assert "error" in statuses
        assert "circuit_open" in statuses

    @pytest.mark.anyio
    async def test_circuit_breaker_state_visible_in_health_endpoint(
        self, client_with_browser, monkeypatch
    ):
        """GET /health reports circuit breaker states."""
        resp = await client_with_browser.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "circuit_breakers" in data
        breaker_names = {b["tool"] for b in data["circuit_breakers"]}
        assert BROWSER_TOOL in breaker_names

        # All start CLOSED
        for b in data["circuit_breakers"]:
            if b["tool"] == BROWSER_TOOL:
                assert b["state"] == "CLOSED"


# ---------------------------------------------------------------------------
# Section 8 — Negative cases / security
# ---------------------------------------------------------------------------


class TestNegativeCases:
    """Negative cases: bad role, bad tool, no auth, schema errors."""

    @pytest.mark.anyio
    async def test_unknown_tool_returns_not_found(self, client):
        payload = {
            "agent_id": "w1",
            "sender_role": "worker",
            "tool_name": "nonexistent.tool",
            "kwargs": {},
        }
        resp = await client.post("/tools/execute", json=payload)
        data = resp.json()
        assert data["success"] is False
        assert data["error_code"] == "TOOL_NOT_FOUND"

    @pytest.mark.anyio
    async def test_sub_agent_blocked_from_document_create(self, make_registry):
        resp = await make_registry().execute(
            _req(DOC_CREATE_TOOL, caller_id="sa", role=AgentRole.SUB_AGENT)
        )
        assert resp.success is False
        assert resp.error_code == "FORBIDDEN"

    @pytest.mark.anyio
    async def test_worker_blocked_from_infra_provision(self, make_registry):
        resp = await make_registry().execute(
            _req("infra.provision", caller_id="w1", role=AgentRole.WORKER)
        )
        assert resp.success is False
        assert resp.error_code == "FORBIDDEN"

    @pytest.mark.anyio
    async def test_path_body_tool_name_mismatch_returns_400(self, client):
        payload = {
            "agent_id": "w1",
            "sender_role": "worker",
            "tool_name": "blob.download",  # mismatches path
            "kwargs": {},
        }
        resp = await client.post(f"/tools/{SEARCH_TOOL}/run", json=payload)
        assert resp.status_code == 400

    @pytest.mark.anyio
    async def test_invalid_role_returns_422(self, client):
        payload = {
            "agent_id": "w1",
            "sender_role": "nonexistent_role",
            "tool_name": SEARCH_TOOL,
            "kwargs": {},
        }
        resp = await client.post("/tools/execute", json=payload)
        assert resp.status_code == 422

    @pytest.mark.anyio
    async def test_grant_endpoint_without_tool_name_returns_422(self, client):
        resp = await client.post(f"/tools/workers/{WORKER_ID}/grants", json={})
        assert resp.status_code == 422

    @pytest.mark.anyio
    async def test_tool_response_contains_idempotency_key(self, make_registry):
        import uuid

        key = uuid.uuid4()
        req = ToolRequest(
            caller_id="w1",
            caller_role=AgentRole.WORKER,
            tool_name=SEARCH_TOOL,
            tool_kwargs={"query": "idempotency"},
            idempotency_key=key,
        )
        resp = await make_registry().execute(req)
        assert resp.success is True
        assert resp.idempotency_key == key

    @pytest.mark.anyio
    async def test_tool_response_carries_project_id_in_audit(self, make_registry):
        registry = make_registry()
        pid = "proj-audit-check"
        await registry.execute(_req(SEARCH_TOOL, caller_id="pid-worker", project_id=pid))
        logs = registry.get_audit_log(worker_id="pid-worker")
        assert logs[0]["project_id"] == pid
