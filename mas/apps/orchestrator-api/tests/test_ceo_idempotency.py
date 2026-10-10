"""Router publication deduplication must not suppress command execution."""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import anyio
import pytest


@pytest.mark.anyio
async def test_ceo_request_reuses_model_snapshot_across_router_retry(monkeypatch) -> None:
    from orchestrator_api import main

    class Storage:
        def __init__(self) -> None:
            self.values: dict[str, str] = {}

        async def get_config(self, key: str):
            return self.values.get(key)

        async def set_config_if_absent(self, key: str, value: str) -> bool:
            if key in self.values:
                return False
            self.values[key] = value
            return True

        async def compare_and_set_config(self, key: str, expected: str, value: str) -> bool:
            if self.values.get(key) != expected:
                return False
            self.values[key] = value
            return True

    storage = Storage()
    previous = main.app.state.storage
    main.app.state.storage = storage
    generated = [uuid4(), uuid4()]
    calls = 0

    async def attach(envelope: dict[str, object]) -> None:
        nonlocal calls
        envelope["model_resolution_snapshot_id"] = str(generated[calls])
        calls += 1

    monkeypatch.setattr(main, "_attach_governance_model_snapshot", attach)
    try:
        message_id = "ceo-retry-snapshot"
        first = {"message_id": message_id, "msg_type": "TASK"}
        first_snapshot = await main._prepare_ceo_request_model_snapshot(
            first,
            message_id=message_id,
            durable_record=None,
        )
        second = {"message_id": message_id, "msg_type": "TASK"}
        second_snapshot = await main._prepare_ceo_request_model_snapshot(
            second,
            message_id=message_id,
            durable_record=None,
        )
    finally:
        main.app.state.storage = previous

    assert first_snapshot == generated[0]
    assert second_snapshot == generated[0]
    assert second["model_resolution_snapshot_id"] == str(generated[0])
    assert calls == 1


@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio", "trio"])
async def test_deduplicated_async_ceo_request_is_still_scheduled(
    client, monkeypatch, anyio_backend: str
) -> None:
    from orchestrator_api import main

    monkeypatch.setenv("ROUTER_SECRET", "test-router-secret")

    class DurableStorage:
        def __init__(self) -> None:
            self.values: dict[str, str] = {}

        async def get_config(self, key: str):
            return self.values.get(key)

        async def set_config_if_absent(self, key: str, value: str) -> bool:
            if key in self.values:
                return False
            self.values[key] = value
            return True

        async def compare_and_set_config(self, key: str, expected: str, value: str) -> bool:
            if self.values.get(key) != expected:
                return False
            self.values[key] = value
            return True

        async def get_all_config(self):
            return dict(self.values)

    monkeypatch.setattr(main.app.state, "storage", DurableStorage())

    class RouterResponse:
        status_code = 200
        is_success = True
        text = ""

        def json(self):
            return {"entry_id": "1700000000000-0", "deduplicated": True}

    class RouterClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args) -> None:
            return None

        async def post(self, *args, **kwargs):
            return RouterResponse()

    monkeypatch.setattr(main.httpx, "AsyncClient", RouterClient)
    handle = AsyncMock(return_value={"type": "project_create", "response": "created"})
    monkeypatch.setattr(main, "_handle_ceo_operator_intent", handle)
    monkeypatch.setattr(main, "_publish_ceo_chat_progress", AsyncMock())
    monkeypatch.setattr(main, "_publish_ceo_chat_response", AsyncMock())
    assert anyio_backend in {"asyncio", "trio"}

    response = await client.post(
        "/ceo/message",
        headers={"X-API-Key": "test-operator-key"},
        json={
            "message": "create a project named dedupe test",
            "request_id": "b870d9a2-8695-4f6e-870a-6f205202bdde",
            "async_mode": True,
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    handle.assert_awaited_once()

    retry = await client.post(
        "/ceo/message",
        headers={"X-API-Key": "test-operator-key"},
        json={
            "message": "create a project named dedupe test",
            "request_id": "b870d9a2-8695-4f6e-870a-6f205202bdde",
            "async_mode": True,
        },
    )

    assert retry.status_code == 200
    assert retry.json()["status"] == "duplicate"
    handle.assert_awaited_once()

    changed_mode = await client.post(
        "/ceo/message",
        headers={"X-API-Key": "test-operator-key"},
        json={
            "message": "create a project named dedupe test",
            "request_id": "b870d9a2-8695-4f6e-870a-6f205202bdde",
            "async_mode": True,
            "project_start_mode": "prepare_only",
        },
    )

    assert changed_mode.status_code == 409
    assert "different CEO command" in changed_mode.text
    handle.assert_awaited_once()

    recovered_id = "00000000-0000-4000-a000-000000000099"
    await main._store_new_ceo_command(
        main.app.state.storage,
        message_id=recovered_id,
        instruction="create a project named recovered command",
        principal="operator",
        context_worker_id=None,
        context_confirmation_token=None,
        project_start_mode="prepare_only",
    )
    async with anyio.create_task_group() as task_group:
        monkeypatch.setattr(main.app.state, "ceo_command_task_group", task_group)
        await main._recover_ceo_commands(main.app.state.storage)

    recovered = await main._load_ceo_command(main.app.state.storage, recovered_id)
    assert recovered is not None
    assert recovered[0]["status"] == "COMPLETED"
    assert handle.await_count == 2
    assert handle.await_args_list[-1].kwargs["project_start_mode"] == "prepare_only"


@pytest.mark.anyio
async def test_recovered_ceo_project_creation_reuses_message_idempotency_key(monkeypatch) -> None:
    from orchestrator_api import main

    class ProjectStorage:
        def __init__(self) -> None:
            self.projects: dict[UUID, dict[str, object]] = {}
            self.create_calls: list[dict[str, object]] = []

        async def get_config(self, key: str):
            return None

        async def get_project(self, project_id: UUID):
            return self.projects.get(project_id)

        async def create_project(self, **kwargs):
            self.create_calls.append(kwargs)
            project_id = kwargs.get("project_id", uuid4())
            project = {
                "id": project_id,
                "name": kwargs["name"],
                "description": kwargs["description"],
                "state": "INIT",
                "created_by": kwargs["created_by"],
                "config": kwargs["config"],
            }
            self.projects[project_id] = project
            return project

    storage = ProjectStorage()
    previous_storage = main.app.state.storage
    main.app.state.storage = storage
    progress = AsyncMock()
    response = AsyncMock()
    monkeypatch.setattr(main, "_publish_ceo_chat_progress", progress)
    monkeypatch.setattr(main, "_publish_ceo_chat_response", response)

    message_id = str(uuid4())
    expected_project_id = uuid5(
        NAMESPACE_URL,
        f"aiat:project-create:{main.DEFAULT_COMPANY_ID}:{message_id}",
    )
    command = {
        "instruction": "Create a project named Expense Analyzer for CSV expense totals and tests.",
        "context_worker_id": None,
        "context_confirmation_token": None,
        "message_id": message_id,
        "principal": "operator",
        "project_start_mode": "prepare_only",
    }
    try:
        # Simulate recovery after project creation but before the CEO command
        # receipt was committed.
        await main._process_ceo_operator_intent(**command)
        await main._process_ceo_operator_intent(**command)
    finally:
        main.app.state.storage = previous_storage

    assert len(storage.create_calls) == 1
    assert storage.create_calls[0]["project_id"] == expected_project_id
    assert len(storage.projects) == 1
    assert response.await_count == 2
