"""Opt-in orchestrator startup mode for safe read-only inspection."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI


class _Result:
    @staticmethod
    def scalar() -> int:
        return 0


class _Connection:
    async def __aenter__(self) -> _Connection:
        return self

    async def __aexit__(self, *_args: object) -> bool:
        return False

    async def execute(self, *_args: object) -> _Result:
        return _Result()


def _storage() -> MagicMock:
    storage = MagicMock()
    storage.connect = AsyncMock()
    storage.close = AsyncMock()
    storage.set_config = AsyncMock()
    storage.record_api_request_observation = AsyncMock()
    storage.get_config = AsyncMock(
        side_effect=lambda key: {
            "system_state": "RUNNING",
            "schedule_enabled": "false",
            "default_company_seeded": "false",
        }.get(key)
    )
    storage.engine.connect = MagicMock(return_value=_Connection())
    return storage


@pytest.mark.anyio
async def test_normal_health_response_does_not_change(client) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.anyio
async def test_maintenance_lifespan_connects_for_reads_without_startup_work(
    client, monkeypatch
) -> None:
    from orchestrator_api import main

    storage = _storage()
    project_id = "00000000-0000-4000-a000-000000000001"
    storage.get_project = AsyncMock(
        return_value={"id": project_id, "name": "Inspection project", "state": "IN_PROGRESS"}
    )
    monkeypatch.setenv("AIAT_STARTUP_MAINTENANCE", "true")
    monkeypatch.setattr(main, "AgentStorage", lambda **_kwargs: storage)
    resume = AsyncMock()
    company_seed = AsyncMock()
    command_recovery = AsyncMock()
    monkeypatch.setattr(main, "run_resume_sequence", resume)
    monkeypatch.setattr(main, "_apply_default_company_manifest", company_seed)
    monkeypatch.setattr(main, "_recover_ceo_commands", command_recovery)

    lifespan_context = main.lifespan(main.app)
    with monkeypatch.context() as startup_patch:
        startup_patch.setattr(
            main.anyio,
            "create_task_group",
            lambda: pytest.fail("maintenance startup must not create a task group"),
        )
        await lifespan_context.__aenter__()
    try:
        assert main.app.state.startup_maintenance is True
        health = await client.get("/health")
        api_health = await client.get("/api/v1/health")
        status = await client.get("/api/v1/system/status")
        project = await client.get(f"/api/v1/projects/{project_id}")
        head_health = await client.head("/health")
        api_head_health = await client.head("/api/v1/health")

        assert health.status_code == 200
        assert health.json() == {"status": "ok", "startup_mode": "maintenance"}
        assert api_health.status_code == 200
        assert api_health.json() == {"status": "ok", "startup_mode": "maintenance"}
        assert status.status_code == 200
        assert status.json()["startup_mode"] == "maintenance"
        assert status.json()["state"] == "RUNNING"
        assert project.status_code == 200
        assert project.json()["name"] == "Inspection project"
        assert head_health.status_code != 503
        assert api_head_health.status_code != 503
    finally:
        await lifespan_context.__aexit__(None, None, None)

    storage.connect.assert_awaited_once()
    storage.get_config.assert_any_await(main.DASHBOARD_SECTION_ACL_CONFIG_KEY)
    storage.set_config.assert_not_awaited()
    storage.record_api_request_observation.assert_not_awaited()
    resume.assert_not_awaited()
    company_seed.assert_not_awaited()
    command_recovery.assert_not_awaited()
    storage.close.assert_awaited_once()


@pytest.mark.anyio
@pytest.mark.parametrize("value", ["TRUE", "1", " true", "false ", ""])
async def test_invalid_maintenance_setting_fails_before_storage_or_tasks(
    client, monkeypatch, value: str
) -> None:
    from orchestrator_api import main

    monkeypatch.setenv("AIAT_STARTUP_MAINTENANCE", value)
    storage_factory = MagicMock(side_effect=AssertionError("storage must not be opened"))
    task_group_factory = MagicMock(side_effect=AssertionError("task group must not start"))
    monkeypatch.setattr(main, "AgentStorage", storage_factory)
    monkeypatch.setattr(main.anyio, "create_task_group", task_group_factory)

    with pytest.raises(RuntimeError, match="must be exactly 'true' or 'false'"):
        async with main.lifespan(main.app):
            pytest.fail("invalid configuration must not enter the lifespan")

    storage_factory.assert_not_called()
    task_group_factory.assert_not_called()


@pytest.mark.anyio
async def test_maintenance_blocks_mutations_and_unsafe_gets_before_handlers(
    client, monkeypatch
) -> None:
    from orchestrator_api import main

    main.app.state.startup_maintenance = True
    storage = _storage()
    main.app.state.storage = storage
    resume = AsyncMock()
    repository_tool = AsyncMock()
    monkeypatch.setattr(main, "run_resume_sequence", resume)
    monkeypatch.setattr(main, "_invoke_project_repository_tool", repository_tool)

    for method in ("POST", "PUT", "PATCH", "DELETE"):
        mutation = await client.request(
            method,
            "/system/resume",
            headers={"X-API-Key": "invalid"},
        )
        assert mutation.status_code == 503
        assert mutation.json()["code"] == "STARTUP_MAINTENANCE"
        assert mutation.headers["X-AIAT-Startup-Mode"] == "maintenance"
    storage.set_config.assert_not_awaited()
    resume.assert_not_awaited()

    for path in (
        "/projects/00000000-0000-4000-a000-000000000001/repository",
        "/api/v1/projects/00000000-0000-4000-a000-000000000001/repository",
        "/projects/00000000-0000-4000-a000-000000000001/documents/00000000-0000-4000-a000-000000000002/preview",
        "/projects/00000000-0000-4000-a000-000000000001/documents/00000000-0000-4000-a000-000000000002/download",
        "/capabilities/workers/00000000-0000-4000-a000-000000000001/upstream",
        "/integrations/connections/00000000-0000-4000-a000-000000000001/health",
        "/integrations/connections/00000000-0000-4000-a000-000000000001/capabilities",
        "/integrations/connections/00000000-0000-4000-a000-000000000001/doctor",
        "/v1/models",
    ):
        response = await client.get(path)
        assert response.status_code == 503, path
        assert response.json()["code"] == "STARTUP_MAINTENANCE"

    repository_tool.assert_not_awaited()


@pytest.mark.anyio
async def test_maintenance_blocks_credentials_ddl_and_unreviewed_get_routes(
    client, monkeypatch
) -> None:
    from orchestrator_api import main

    main.app.state.startup_maintenance = True
    manager = MagicMock()
    manager.ensure_tables = AsyncMock()
    manager.list = AsyncMock(return_value=[])
    manager.get = AsyncMock(return_value=None)
    manager_factory = MagicMock(return_value=manager)
    monkeypatch.setattr(main, "_credentials_manager", manager_factory)
    gate = MagicMock()
    gate.ensure_tables = AsyncMock()
    gate_factory = MagicMock(return_value=gate)
    monkeypatch.setattr(main, "_priv_gate", gate_factory)

    for path in (
        "/credentials",
        "/api/v1/credentials",
        "/credentials/rotation-key",
        "/credentials/rotation-key/audit",
        "/credentials-audit",
        "/ceo/privileged-actions/pending",
        "/ceo/privileged-actions/audit",
    ):
        response = await client.get(path)
        assert response.status_code == 503
        assert response.json()["code"] == "STARTUP_MAINTENANCE"
    manager_factory.assert_not_called()
    manager.ensure_tables.assert_not_awaited()
    gate_factory.assert_not_called()
    gate.ensure_tables.assert_not_awaited()
    head_credentials = await client.head("/credentials")
    assert head_credentials.status_code == 503

    called: list[bool] = []

    async def future_side_effectful_get() -> dict[str, bool]:
        called.append(True)
        return {"executed": True}

    path = "/_maintenance/future-get"
    main.app.add_api_route(path, future_side_effectful_get, methods=["GET"])
    route = next(route for route in main.app.router.routes if getattr(route, "path", None) == path)
    try:
        response = await client.get(path)
    finally:
        main.app.router.routes.remove(route)

    assert response.status_code == 503
    assert response.json()["code"] == "STARTUP_MAINTENANCE"
    assert called == []


@pytest.mark.anyio
async def test_maintenance_denies_new_literal_route_before_allowed_parameter_route(client) -> None:
    from orchestrator_api import main

    main.app.state.startup_maintenance = True
    called: list[bool] = []

    async def future_literal_get() -> dict[str, bool]:
        called.append(True)
        return {"executed": True}

    path = "/flows/__maintenance_literal_probe__"
    main.app.add_api_route(path, future_literal_get, methods=["GET"])
    route = next(route for route in main.app.router.routes if getattr(route, "path", None) == path)
    routes = main.app.router.routes
    routes.remove(route)
    parameter_route_index = next(
        index
        for index, item in enumerate(routes)
        if getattr(item, "path", None) == "/flows/{flow_id}"
    )
    routes.insert(parameter_route_index, route)
    try:
        response = await client.get(path)
    finally:
        routes.remove(route)

    assert response.status_code == 503
    assert response.json()["code"] == "STARTUP_MAINTENANCE"
    assert called == []


@pytest.mark.anyio
async def test_maintenance_rejects_websockets_before_routing(client) -> None:
    from orchestrator_api import main

    main.app.state.startup_maintenance = True
    sent: list[dict[str, object]] = []

    async def receive() -> dict[str, object]:
        return {"type": "websocket.connect"}

    async def send(message: dict[str, object]) -> None:
        sent.append(message)

    scope = {
        "type": "websocket",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "scheme": "ws",
        "path": "/events",
        "raw_path": b"/events",
        "root_path": "",
        "query_string": b"",
        "headers": [],
        "client": ("test", 1234),
        "server": ("test", 80),
        "subprotocols": [],
    }

    await main.app(scope, receive, send)
    assert sent == [{"type": "websocket.close", "code": 1013}]


@pytest.mark.anyio
async def test_normal_lifespan_still_runs_resume_and_bootstrap(monkeypatch) -> None:
    from orchestrator_api import main

    from mas_core import llm_gateway
    from mas_core.worker_registry.local_host_queue_runtime import LocalHostQueueConfig

    storage = _storage()
    monkeypatch.setenv("AIAT_STARTUP_MAINTENANCE", "false")
    monkeypatch.setenv("WORKERS_DIR", "__missing_workers_for_normal_startup_test__")
    monkeypatch.setattr(main, "AgentStorage", lambda **_kwargs: storage)
    resume = AsyncMock(return_value=0)
    company_seed = AsyncMock(return_value=None)
    command_recovery = AsyncMock()
    monkeypatch.setattr(main, "run_resume_sequence", resume)
    monkeypatch.setattr(main, "_apply_default_company_manifest", company_seed)
    monkeypatch.setattr(main, "_recover_ceo_commands", command_recovery)
    monkeypatch.setattr(
        llm_gateway,
        "seed_default_model_profiles",
        AsyncMock(return_value={"status": "pass"}),
    )
    monkeypatch.setattr(LocalHostQueueConfig, "from_environment", lambda _cls=None: None)

    class _TaskGroup:
        def __init__(self) -> None:
            self.started: list[str] = []
            self.cancel_scope = SimpleNamespace(cancel=MagicMock())

        async def __aenter__(self) -> _TaskGroup:
            return self

        async def __aexit__(self, *_args: object) -> bool:
            return False

        def start_soon(self, func: object, *_args: object) -> None:
            self.started.append(getattr(func, "__name__", "unknown"))

    task_group = _TaskGroup()
    monkeypatch.setattr(main.anyio, "create_task_group", lambda: task_group)
    app = FastAPI()

    async with main.lifespan(app):
        assert app.state.startup_maintenance is False
        assert task_group.started

    resume.assert_awaited_once_with(storage)
    company_seed.assert_awaited_once_with(storage)
    command_recovery.assert_awaited_once_with(storage)
    assert [call.args for call in storage.set_config.await_args_list if call.args[0] == "system_state"] == [
        ("system_state", "STARTING"),
        ("system_state", "RUNNING"),
    ]
    task_group.cancel_scope.cancel.assert_called_once()
    storage.close.assert_awaited_once()
