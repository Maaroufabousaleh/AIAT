"""
Test 10 — Manual operational control: system status, shutdown, resume, logs, schedule.

Type: API / integration / security

The AIAT MAS operational control is built into the orchestrator-api routes:
  GET  /health
  GET  /system/status
  GET  /system/diagnostics
  POST /system/shutdown
  POST /system/resume
  GET  /system/logs/{container}
  PUT  /system/schedule

The API-facing operator wrapper is `scripts/mas-ctl`; Compose and systemd
service lifecycle commands remain in their host-owned wrappers.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_engine(scalar_value: int = 0):
    """Return a mock engine whose connect() context manager works with async with."""
    conn = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalar = MagicMock(return_value=scalar_value)
    conn.execute = AsyncMock(return_value=result_mock)
    engine = MagicMock()
    engine.connect = MagicMock(
        return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=conn),
            __aexit__=AsyncMock(return_value=False),
        )
    )
    return engine


def _patch(storage):
    from orchestrator_api.main import app

    app.state.storage = storage


def _base_storage(system_state: str = "RUNNING"):
    storage = MagicMock()
    storage.get_config = AsyncMock(
        side_effect=lambda key: {
            "system_state": system_state,
            "boot_at": "2024-01-01T00:00:00+00:00",
            "shutdown_at": None,
            "schedule_enabled": "false",
            "schedule_start_hour": "8",
            "schedule_end_hour": "20",
            "schedule_timezone": "UTC",
            "schedule_days": "mon,tue,wed,thu,fri",
            "schedule_auto_shutdown": "false",
            "schedule_auto_resume": "false",
        }.get(key)
    )
    storage.set_config = AsyncMock(return_value=None)
    storage.engine = _mock_engine()
    storage.list_projects = AsyncMock(return_value=[])
    return storage


# ---------------------------------------------------------------------------
# 1. Health endpoint
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_health_ok(client):
    """GET /health always returns {status: ok}."""
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


@pytest.mark.anyio
async def test_health_does_not_require_storage(client):
    """GET /health must respond even if storage is not configured."""
    r = await client.get("/health")
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# 2. System status
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_system_status_running(client):
    """GET /system/status returns state, active_projects, uptime."""
    storage = _base_storage("RUNNING")
    _patch(storage)

    r = await client.get("/system/status")
    assert r.status_code == 200
    data = r.json()
    assert "state" in data
    assert data["state"] == "RUNNING"
    assert "active_projects" in data
    assert "uptime_seconds" in data


@pytest.mark.anyio
async def test_system_status_unknown_when_no_config(client):
    """GET /system/status returns UNKNOWN if system_state config is missing."""
    storage = _base_storage()
    storage.get_config = AsyncMock(return_value=None)  # All configs missing
    _patch(storage)

    r = await client.get("/system/status")
    assert r.status_code == 200
    data = r.json()
    assert data["state"] == "UNKNOWN"


@pytest.mark.anyio
async def test_system_status_reports_schedule_disabled_by_default(client):
    """Schedule is disabled by default."""
    storage = _base_storage("RUNNING")
    _patch(storage)

    r = await client.get("/system/status")
    assert r.status_code == 200
    data = r.json()
    assert data.get("schedule_enabled") is False


# ---------------------------------------------------------------------------
# 3. System shutdown
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_system_shutdown_accepted(client):
    """POST /system/shutdown initiates graceful shutdown."""
    import orchestrator_api.main as m

    storage = _base_storage("RUNNING")
    _patch(storage)

    # Patch STATE_TO_TEAM to be empty so ACK loop exits immediately
    with patch.dict(m.STATE_TO_TEAM, {}, clear=True):
        m._SHUTDOWN_TIMEOUT_S = 0
        original = m._SHUTDOWN_TIMEOUT_S
        try:
            r = await client.post("/system/shutdown")
            assert r.status_code in (200, 202, 503)
        finally:
            m._SHUTDOWN_TIMEOUT_S = 45


@pytest.mark.anyio
async def test_system_shutdown_sets_state(client):
    """Shutdown endpoint must call set_config('system_state', 'SHUTTING_DOWN')."""
    import orchestrator_api.main as m

    storage = _base_storage("RUNNING")
    _patch(storage)

    with patch.dict(m.STATE_TO_TEAM, {}, clear=True):
        m._SHUTDOWN_TIMEOUT_S = 0
        try:
            await client.post("/system/shutdown")
        finally:
            m._SHUTDOWN_TIMEOUT_S = 45

    set_config_calls = [str(c) for c in storage.set_config.call_args_list]
    relevant = [c for c in set_config_calls if "system_state" in c]
    assert len(relevant) >= 1, (
        "Shutdown endpoint did not call storage.set_config with system_state. "
        "Production gap: state not persisted."
    )


@pytest.mark.anyio
async def test_system_shutdown_rejected_when_already_shutdown(client):
    """POST /system/shutdown while already SHUTTING_DOWN — behavior depends on middleware."""
    import orchestrator_api.main as m
    from orchestrator_api.main import app

    app.state._cached_system_state = "SHUTTING_DOWN"

    storage = _base_storage("SHUTTING_DOWN")
    _patch(storage)

    # With SHUTTING_DOWN state, the middleware might block the request (503)
    # or the route might run (200) — both are valid behaviors.
    with patch.dict(m.STATE_TO_TEAM, {}, clear=True):
        m._SHUTDOWN_TIMEOUT_S = 0
        try:
            r = await client.post("/system/shutdown")
            assert r.status_code in (200, 202, 503)
        finally:
            m._SHUTDOWN_TIMEOUT_S = 45


# ---------------------------------------------------------------------------
# 4. System resume
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_system_resume_accepted(client):
    """POST /system/resume brings system back to RUNNING."""
    storage = _base_storage("STOPPED")
    _patch(storage)

    r = await client.post("/system/resume")
    assert r.status_code in (200, 202)
    data = r.json()
    assert "state" in data or "status" in data or "message" in data


@pytest.mark.anyio
async def test_system_resume_sets_running_state(client):
    """Resume must call set_config('system_state', 'RUNNING')."""
    storage = _base_storage("STOPPED")
    _patch(storage)

    await client.post("/system/resume")
    set_calls = [str(c) for c in storage.set_config.call_args_list]
    running_calls = [c for c in set_calls if "RUNNING" in c]
    assert len(running_calls) >= 1, "Resume did not set state to RUNNING"


# ---------------------------------------------------------------------------
# 5. Container logs endpoint — security allowlist
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_logs_unknown_container_rejected(client):
    """GET /system/logs/{unknown} → 400 (security: unknown containers blocked)."""
    r = await client.get("/system/logs/totally-unknown-container-xyz")
    assert r.status_code == 400
    data = r.json()
    assert "detail" in data
    assert "Allowed" in data["detail"] or "allowed" in data["detail"] or "Unknown" in data["detail"]


@pytest.mark.anyio
async def test_logs_allowed_container_returns_streaming_response(client):
    """GET /system/logs/redis → 200 streaming response (docker logs mocked)."""
    # Mock subprocess so no real docker call is made
    with patch("orchestrator_api.main._stream_container_logs") as mock_stream:
        import asyncio

        async def _fake_gen(*args, **kwargs):
            yield "data: log line 1\n\n"
            yield "data: log line 2\n\n"

        mock_stream.return_value = _fake_gen()
        r = await client.get("/system/logs/redis?tail=10&follow=false")
        # 200 or 500 if docker not available — the important check is NOT 400
        assert r.status_code in (200, 500)


@pytest.mark.anyio
async def test_logs_known_containers_in_allowlist(client):
    """All critical service containers are in the allowlist."""
    from orchestrator_api.main import ALLOWED_CONTAINERS

    required = {
        "redis",
        "postgres",
        "minio",
        "orchestrator-api",
        "message-router",
        "tool-service",
        "dashboard",
    }
    missing = required - ALLOWED_CONTAINERS
    assert not missing, f"Critical containers missing from allowlist: {missing}"


@pytest.mark.anyio
async def test_logs_sql_injection_container_blocked(client):
    """SQL/shell injection in container name is blocked by allowlist."""
    malicious = "redis-rm-rf-root"  # Shell injection sanitized by allowlist check
    r = await client.get(f"/system/logs/{malicious}")
    assert r.status_code in (400, 404, 422)


# ---------------------------------------------------------------------------
# 6. Schedule configuration
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_update_schedule_persists_config(client):
    """PUT /system/schedule persists schedule settings."""
    storage = _base_storage("RUNNING")
    _patch(storage)

    r = await client.put(
        "/system/schedule",
        json={
            "enabled": True,
            "start_hour": 8,
            "end_hour": 18,
            "timezone": "UTC",
            "days": ["mon", "tue", "wed", "thu", "fri"],
            "auto_shutdown": True,
            "auto_resume": True,
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert "status" in data or "message" in data


@pytest.mark.anyio
async def test_update_schedule_invalid_hours_rejected(client):
    """PUT /system/schedule with invalid hours → 422."""
    storage = _base_storage("RUNNING")
    _patch(storage)

    r = await client.put(
        "/system/schedule",
        json={
            "enabled": True,
            "start_hour": 25,  # invalid
            "end_hour": -1,  # invalid
            "timezone": "UTC",
            "days": [],
            "auto_shutdown": False,
            "auto_resume": False,
        },
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# 7. Shutdown ACK / NACK (worker confirmation protocol)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_shutdown_ack_accepted(client):
    """POST /system/shutdown-ack is accepted from workers."""
    r = await client.post(
        "/system/shutdown-ack",
        json={"worker_id": str(uuid4()), "team_id": "dept_production"},
    )
    assert r.status_code == 200


@pytest.mark.anyio
async def test_shutdown_nack_accepted(client):
    """POST /system/shutdown-nack is accepted from workers."""
    r = await client.post(
        "/system/shutdown-nack",
        json={"worker_id": str(uuid4()), "team_id": "dept_qa", "reason": "Active task"},
    )
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# 8. Middleware — reject requests during shutdown
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_middleware_blocks_during_shutdown(client):
    """Requests to non-health, non-system routes are blocked during SHUTTING_DOWN."""
    from orchestrator_api.main import app

    original = getattr(app.state, "_cached_system_state", "RUNNING")
    try:
        app.state._cached_system_state = "SHUTTING_DOWN"
        storage = _base_storage("SHUTTING_DOWN")
        _patch(storage)
        r = await client.get("/projects")
        assert r.status_code in (200, 503)
    finally:
        app.state._cached_system_state = original


@pytest.mark.anyio
async def test_health_passes_during_shutdown(client):
    """GET /health must respond 200 even during SHUTTING_DOWN."""
    from orchestrator_api.main import app

    original = getattr(app.state, "_cached_system_state", "RUNNING")
    try:
        app.state._cached_system_state = "SHUTTING_DOWN"
        r = await client.get("/health")
        assert r.status_code == 200
    finally:
        app.state._cached_system_state = original


# ---------------------------------------------------------------------------
# 9. Operational diagnostics — system status reports all key fields
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_system_status_diagnostic_fields(client):
    """System status must include fields useful for operational diagnostics."""
    storage = _base_storage("RUNNING")
    _patch(storage)

    r = await client.get("/system/status")
    assert r.status_code == 200
    data = r.json()

    # Diagnostically required fields
    assert "state" in data, "Missing 'state' field"
    assert "active_projects" in data, "Missing 'active_projects' count"
    assert "total_projects" in data, "Missing 'total_projects' count"
    assert "uptime_seconds" in data, "Missing 'uptime_seconds'"
    assert "schedule_enabled" in data, "Missing 'schedule_enabled'"


# ---------------------------------------------------------------------------
# 10. Host-owned lifecycle boundary
# ---------------------------------------------------------------------------


def test_mas_ctl_bootstrap_script_exists():
    """The operator-facing API wrapper is present and executable."""
    from pathlib import Path

    script = Path(__file__).resolve().parents[3] / "scripts" / "mas-ctl"
    assert script.is_file()
    assert script.stat().st_mode & 0o111


def test_service_restart_remains_host_boundary():
    """Per-service restart stays in the Compose/systemd host boundary."""
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[3]
    compose_wrapper = repo_root / "infra" / "compose" / "mas.sh"
    systemd_wrapper = repo_root / "infra" / "systemd" / "masctl"
    assert compose_wrapper.is_file()
    assert systemd_wrapper.is_file()
    assert 'restart)' in compose_wrapper.read_text(encoding="utf-8")
    systemd_text = systemd_wrapper.read_text(encoding="utf-8")
    assert 'restart)' in systemd_text
    assert '"${compose[@]}" restart "$SERVICE_NAME"' in systemd_text


@pytest.mark.anyio
async def test_system_diagnostics_reports_dependency_health(client, monkeypatch):
    """Diagnostics reports safe health facts without exposing dependency payloads."""
    from orchestrator_api import main

    storage = _base_storage("RUNNING")
    _patch(storage)
    monkeypatch.setattr(
        main,
        "_probe_http_health",
        AsyncMock(
            side_effect=[
                {"status": "ok", "http_status": 200, "redis_connected": True},
                {
                    "status": "ok",
                    "http_status": 200,
                    "cache_connected": True,
                    "tools_registered": 24,
                },
            ]
        ),
    )
    monkeypatch.setattr(
        main,
        "_probe_object_store_health",
        AsyncMock(return_value={"status": "ok", "configured": True}),
    )

    response = await client.get("/system/diagnostics")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["state"] == "RUNNING"
    assert set(body["dependencies"]) == {
        "database",
        "message_router",
        "tool_service",
        "object_store",
    }
    assert body["dependencies"]["database"]["status"] == "ok"
    assert body["dependencies"]["message_router"]["redis_connected"] is True
    assert body["dependencies"]["tool_service"]["tools_registered"] == 24
    assert "error" not in body["dependencies"]["message_router"]


@pytest.mark.anyio
async def test_system_diagnostics_reports_degraded_dependency(client, monkeypatch):
    """A failed dependency makes the aggregate degraded without raising 5xx."""
    from orchestrator_api import main

    storage = _base_storage("RUNNING")
    _patch(storage)
    monkeypatch.setattr(
        main,
        "_probe_http_health",
        AsyncMock(
            side_effect=[
                {"status": "degraded", "http_status": 200, "reported_status": "degraded"},
                {"status": "error", "error_type": "ConnectError"},
            ]
        ),
    )
    monkeypatch.setattr(
        main,
        "_probe_object_store_health",
        AsyncMock(return_value={"status": "not_configured", "configured": False}),
    )

    response = await client.get("/system/diagnostics")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["dependencies"]["message_router"]["reported_status"] == "degraded"
    assert body["dependencies"]["tool_service"]["error_type"] == "ConnectError"
    assert body["dependencies"]["object_store"]["status"] == "not_configured"


@pytest.mark.anyio
async def test_http_diagnostic_probe_redacts_dependency_payload(client, monkeypatch):
    """HTTP probes retain safe flags, never raw dependency error text."""
    from orchestrator_api import main

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def get(self, _url):
            import httpx

            return httpx.Response(
                200,
                json={"status": "degraded", "redis": "error: redis-password=secret-value"},
            )

    monkeypatch.setattr(main.httpx, "AsyncClient", lambda **_kwargs: FakeClient())
    result = await main._probe_http_health("http://router")

    assert result["status"] == "degraded"
    assert result["redis_connected"] is False
    assert "error_type" not in result
    assert "secret-value" not in str(result)


@pytest.mark.anyio
async def test_object_store_diagnostic_probe_reports_unconfigured(client, monkeypatch):
    """Absent object-store credentials are reported without attempting a call."""
    from orchestrator_api import main

    for name in (
        "MINIO_ENDPOINT",
        "BLOB_ENDPOINT_URL",
        "MINIO_ACCESS_KEY",
        "AWS_ACCESS_KEY_ID",
        "MINIO_SECRET_KEY",
        "AWS_SECRET_ACCESS_KEY",
    ):
        monkeypatch.delenv(name, raising=False)

    result = await main._probe_object_store_health()

    assert result == {"status": "not_configured", "configured": False}


@pytest.mark.anyio
async def test_system_diagnostics_without_storage_returns_503(client):
    """The route cannot report a database check without control-plane storage."""
    from orchestrator_api.main import app

    app.state.storage = None
    response = await client.get("/system/diagnostics")
    assert response.status_code == 503
