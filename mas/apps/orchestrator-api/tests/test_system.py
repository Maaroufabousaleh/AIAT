"""
Tests for system lifecycle endpoints:
  GET  /system/status
  POST /system/shutdown
  POST /system/resume
  POST /system/shutdown-ack
  POST /system/shutdown-nack
  PUT  /system/schedule
  GET  /teams
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from conftest import NOW_ISO, _fake_project

# ── helpers ───────────────────────────────────────────────────────────────────


def _make_storage(
    system_state="RUNNING",
    boot_at=None,
    schedule_enabled="false",
    projects=None,
):
    storage = MagicMock()

    # Compute expected counts from the projects list
    all_projects = projects if projects is not None else []
    total_count = len(all_projects)
    active_count = sum(
        1 for p in all_projects if p.get("state") not in ("COMPLETED", "ARCHIVED", "FAILED")
    )

    # Properly mock engine.connect() for system_status count queries
    _call_count = {"n": 0}

    async def mock_execute(query):
        _call_count["n"] += 1
        mock_row = MagicMock()
        # First COUNT is total, second is active
        mock_row.scalar = MagicMock(
            return_value=total_count if _call_count["n"] == 1 else active_count
        )
        return mock_row

    _conn = MagicMock()
    _conn.__aenter__ = AsyncMock(return_value=_conn)
    _conn.__aexit__ = AsyncMock(return_value=False)
    _conn.execute = AsyncMock(side_effect=mock_execute)

    storage.engine = MagicMock()
    storage.engine.connect = MagicMock(return_value=_conn)

    config_map = {
        "system_state": system_state,
        "boot_at": boot_at,
        "schedule_enabled": schedule_enabled,
    }

    async def get_config(key):
        return config_map.get(key)

    storage.get_config = AsyncMock(side_effect=get_config)
    storage.set_config = AsyncMock(return_value=None)
    storage.list_projects = AsyncMock(return_value=projects if projects is not None else [])
    return storage


def _patch_state(storage, controller=None):
    """Directly set app.state attributes."""
    from orchestrator_api.main import app

    app.state.storage = storage
    if controller is not None:
        app.state.controller = controller


# ── GET /system/status ────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_system_status_running(client):
    """GET /system/status returns current system state."""
    _patch_state(_make_storage(system_state="RUNNING", projects=[]))

    resp = await client.get("/system/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["state"] == "RUNNING"
    assert "active_projects" in data
    assert "total_projects" in data
    assert "uptime_seconds" in data
    assert "schedule_enabled" in data


@pytest.mark.anyio
async def test_system_status_with_boot_at(client):
    """GET /system/status computes uptime when boot_at is set."""
    _patch_state(_make_storage(system_state="RUNNING", boot_at=NOW_ISO, projects=[]))

    resp = await client.get("/system/status")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data["uptime_seconds"], float)
    assert data["uptime_seconds"] >= 0


@pytest.mark.anyio
async def test_system_status_counts_active_projects(client):
    """GET /system/status correctly counts active vs total projects."""
    projects = [
        _fake_project("IN_PROGRESS"),
        _fake_project("COMPLETED"),
        _fake_project("FAILED"),
        _fake_project("FEASIBILITY_CHECK"),
    ]
    _patch_state(_make_storage(system_state="RUNNING", projects=projects))

    resp = await client.get("/system/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_projects"] == 4
    # IN_PROGRESS and FEASIBILITY_CHECK are active; COMPLETED and FAILED are not
    assert data["active_projects"] == 2


@pytest.mark.anyio
async def test_system_status_schedule_enabled(client):
    """GET /system/status returns schedule_enabled=True when set."""
    _patch_state(_make_storage(system_state="RUNNING", schedule_enabled="true", projects=[]))

    resp = await client.get("/system/status")
    assert resp.status_code == 200
    assert resp.json()["schedule_enabled"] is True


@pytest.mark.anyio
async def test_system_status_no_storage_returns_503(client):
    """GET /system/status returns 503 when storage unavailable."""
    _patch_state(None)
    resp = await client.get("/system/status")
    assert resp.status_code == 503


# ── POST /system/shutdown ─────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_system_shutdown_happy_path(client):
    """POST /system/shutdown completes shutdown sequence and returns 'stopped'."""
    _patch_state(_make_storage())
    fresh_event = asyncio.Event()

    with (
        patch("httpx.AsyncClient") as mock_http,
        patch("orchestrator_api.main._shutdown_ack_event", fresh_event),
        patch("orchestrator_api.main._SHUTDOWN_TIMEOUT_S", 0),
    ):
        mock_http.return_value.__aenter__ = AsyncMock(
            return_value=MagicMock(post=AsyncMock(return_value=MagicMock(status_code=200)))
        )
        mock_http.return_value.__aexit__ = AsyncMock(return_value=False)

        resp = await client.post("/system/shutdown")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "stopped"
    assert "shutdown_at" in data


@pytest.mark.anyio
async def test_system_shutdown_no_storage_returns_503(client):
    """POST /system/shutdown returns 503 when storage unavailable."""
    _patch_state(None)
    resp = await client.post("/system/shutdown")
    assert resp.status_code == 503


@pytest.mark.anyio
async def test_system_shutdown_sets_state(client):
    """POST /system/shutdown sets system_state to STOPPED in storage."""
    storage = _make_storage()
    _patch_state(storage)
    fresh_event = asyncio.Event()

    with (
        patch("httpx.AsyncClient") as mock_http,
        patch("orchestrator_api.main._shutdown_ack_event", fresh_event),
        patch("orchestrator_api.main._SHUTDOWN_TIMEOUT_S", 0),
    ):
        mock_http.return_value.__aenter__ = AsyncMock(
            return_value=MagicMock(post=AsyncMock(return_value=MagicMock(status_code=200)))
        )
        mock_http.return_value.__aexit__ = AsyncMock(return_value=False)

        await client.post("/system/shutdown")

    # Verify SHUTTING_DOWN and STOPPED were set
    calls = [call.args for call in storage.set_config.await_args_list]
    states_set = [args[1] for args in calls if args[0] == "system_state"]
    assert "SHUTTING_DOWN" in states_set
    assert "STOPPED" in states_set


# ── POST /system/resume ───────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_system_resume_no_active_projects(client):
    """POST /system/resume returns resumed status with 0 when no active projects."""
    _patch_state(_make_storage(projects=[]))

    resp = await client.post("/system/resume")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "resumed"
    assert data["projects_resumed"] == 0


@pytest.mark.anyio
async def test_system_resume_no_storage_returns_503(client):
    """POST /system/resume returns 503 when storage unavailable."""
    _patch_state(None)
    resp = await client.post("/system/resume")
    assert resp.status_code == 503


@pytest.mark.anyio
async def test_system_resume_with_active_projects(client):
    """POST /system/resume attempts to re-publish active projects."""
    projects = [_fake_project("IN_PROGRESS"), _fake_project("FEASIBILITY_CHECK")]
    _patch_state(_make_storage(projects=projects))

    with patch("httpx.AsyncClient") as mock_http:
        mock_response = MagicMock(status_code=201)
        mock_client_instance = MagicMock(post=AsyncMock(return_value=mock_response))
        mock_http.return_value.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_http.return_value.__aexit__ = AsyncMock(return_value=False)

        resp = await client.post("/system/resume")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "resumed"
    assert data["projects_resumed"] == 2


# ── POST /system/shutdown-ack ─────────────────────────────────────────────────


@pytest.mark.anyio
async def test_shutdown_ack_returns_acknowledged(client):
    """POST /system/shutdown-ack returns acknowledged status."""
    resp = await client.post(
        "/system/shutdown-ack",
        json={"team_id": "exec_ceo", "agent_id": "ceo-1"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "acknowledged"


@pytest.mark.anyio
async def test_shutdown_ack_empty_body(client):
    """POST /system/shutdown-ack works with an empty body."""
    resp = await client.post("/system/shutdown-ack", json={})
    assert resp.status_code == 200
    assert resp.json()["status"] == "acknowledged"


# ── PUT /system/schedule ──────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_update_schedule_happy_path(client):
    """PUT /system/schedule stores schedule config successfully."""
    _patch_state(_make_storage())

    resp = await client.put(
        "/system/schedule",
        json={
            "enabled": True,
            "start_hour": 9,
            "end_hour": 17,
            "timezone": "US/Eastern",
            "days": ["mon", "tue", "wed", "thu", "fri"],
            "auto_shutdown": True,
            "auto_resume": True,
        },
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "schedule_updated"


@pytest.mark.anyio
async def test_update_schedule_defaults(client):
    """PUT /system/schedule works with default values."""
    _patch_state(_make_storage())
    resp = await client.put("/system/schedule", json={})
    assert resp.status_code == 200


@pytest.mark.anyio
async def test_update_schedule_invalid_start_hour(client):
    """PUT /system/schedule returns 422 for start_hour > 23."""
    resp = await client.put("/system/schedule", json={"start_hour": 25})
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_update_schedule_no_storage_returns_503(client):
    """PUT /system/schedule returns 503 when storage unavailable."""
    _patch_state(None)
    resp = await client.put("/system/schedule", json={"enabled": True})
    assert resp.status_code == 503


# ── GET /teams ────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_list_teams(client):
    """GET /teams returns all known teams from the state→team mapping."""
    resp = await client.get("/teams")
    assert resp.status_code == 200
    teams = resp.json()
    assert len(teams) > 0
    for team in teams:
        assert "team_id" in team
    team_ids = {t["team_id"] for t in teams}
    assert "exec_ceo" in team_ids
    assert "exec_coo" in team_ids
    assert "office_cto" in team_ids


# ── 503 shutdown guard middleware ─────────────────────────────────────────────


@pytest.mark.anyio
async def test_rejects_new_projects_during_shutting_down(client):
    """POST /projects returns 503 when _cached_system_state is SHUTTING_DOWN."""
    from orchestrator_api.main import app

    _patch_state(_make_storage())
    app.state._cached_system_state = "SHUTTING_DOWN"

    resp = await client.post("/projects", json={"name": "test", "description": "test"})
    assert resp.status_code == 503
    assert "SHUTTING_DOWN" in resp.json()["detail"]


@pytest.mark.anyio
async def test_rejects_new_projects_during_stopped(client):
    """POST /projects returns 503 when _cached_system_state is STOPPED."""
    from orchestrator_api.main import app

    _patch_state(_make_storage())
    app.state._cached_system_state = "STOPPED"

    resp = await client.post("/projects", json={"name": "test", "description": "test"})
    assert resp.status_code == 503


@pytest.mark.anyio
async def test_allows_system_endpoints_during_shutdown(client):
    """GET /system/status and POST /system/shutdown-ack are allowed during shutdown."""
    from orchestrator_api.main import app

    _patch_state(_make_storage())
    app.state._cached_system_state = "SHUTTING_DOWN"

    resp = await client.get("/system/status")
    assert resp.status_code == 200

    resp = await client.post("/system/shutdown-ack", json={"team_id": "exec_ceo"})
    assert resp.status_code == 200


@pytest.mark.anyio
async def test_allows_health_during_shutdown(client):
    """GET /health is allowed during shutdown."""
    from orchestrator_api.main import app

    app.state._cached_system_state = "SHUTTING_DOWN"

    resp = await client.get("/health")
    assert resp.status_code == 200


@pytest.mark.anyio
async def test_allows_get_projects_during_shutdown(client):
    """GET /projects is allowed during shutdown (only POST is blocked)."""
    from orchestrator_api.main import app

    _patch_state(_make_storage())
    app.state._cached_system_state = "SHUTTING_DOWN"

    resp = await client.get("/projects")
    assert resp.status_code == 200


# ── POST /system/shutdown-nack ────────────────────────────────────────────────


@pytest.mark.anyio
async def test_shutdown_nack_returns_received(client):
    """POST /system/shutdown-nack records a failed shutdown."""
    resp = await client.post(
        "/system/shutdown-nack",
        json={
            "team_id": "exec_ceo",
            "agent_id": "ceo-1",
            "reason": "checkpoint_save_failed",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "nack_received"


@pytest.mark.anyio
async def test_shutdown_nack_empty_body(client):
    """POST /system/shutdown-nack works with an empty body."""
    resp = await client.post("/system/shutdown-nack", json={})
    assert resp.status_code == 200
    assert resp.json()["status"] == "nack_received"


# ── ACK-waiting with real timeout ─────────────────────────────────────────────


@pytest.mark.anyio
async def test_shutdown_reports_missing_teams(client):
    """POST /system/shutdown reports teams that did not ACK when timeout=0."""
    _patch_state(_make_storage())
    fresh_event = asyncio.Event()

    with (
        patch("httpx.AsyncClient") as mock_http,
        patch("orchestrator_api.main._shutdown_ack_event", fresh_event),
        patch("orchestrator_api.main._SHUTDOWN_TIMEOUT_S", 0),
    ):
        mock_http.return_value.__aenter__ = AsyncMock(
            return_value=MagicMock(post=AsyncMock(return_value=MagicMock(status_code=200)))
        )
        mock_http.return_value.__aexit__ = AsyncMock(return_value=False)

        resp = await client.post("/system/shutdown")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "stopped"
    assert "missing_teams" in data
    assert "acked_teams" in data
    assert "nacked_teams" in data


# ── APScheduler cron configuration ────────────────────────────────────────────


@pytest.mark.anyio
async def test_configure_schedule_cron_starts_scheduler(client):
    """PUT /system/schedule with enabled=True starts APScheduler."""
    _patch_state(_make_storage())

    with patch("orchestrator_api.main._configure_schedule_cron") as mock_cron:
        resp = await client.put(
            "/system/schedule",
            json={
                "enabled": True,
                "start_hour": 9,
                "end_hour": 17,
                "timezone": "UTC",
                "days": ["mon", "tue", "wed", "thu", "fri"],
                "auto_shutdown": True,
                "auto_resume": True,
            },
        )

    assert resp.status_code == 200
    mock_cron.assert_called_once()


@pytest.mark.anyio
async def test_configure_schedule_cron_disabled(client):
    """PUT /system/schedule with enabled=False stops APScheduler."""
    _patch_state(_make_storage())

    with patch("orchestrator_api.main._configure_schedule_cron") as mock_cron:
        resp = await client.put("/system/schedule", json={"enabled": False})

    assert resp.status_code == 200
    mock_cron.assert_called_once()
