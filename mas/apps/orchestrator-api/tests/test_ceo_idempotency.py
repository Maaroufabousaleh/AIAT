"""Router publication deduplication must not suppress command execution."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest


@pytest.mark.anyio
async def test_deduplicated_async_ceo_request_is_still_scheduled(client, monkeypatch) -> None:
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

    response = await client.post(
        "/ceo/message",
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
        json={
            "message": "create a project named dedupe test",
            "request_id": "b870d9a2-8695-4f6e-870a-6f205202bdde",
            "async_mode": True,
        },
    )

    assert retry.status_code == 200
    assert retry.json()["status"] == "duplicate"
    handle.assert_awaited_once()

    recovered_id = "00000000-0000-4000-a000-000000000099"
    await main._store_new_ceo_command(
        main.app.state.storage,
        message_id=recovered_id,
        instruction="create a project named recovered command",
        context_worker_id=None,
        context_confirmation_token=None,
    )
    await main._recover_ceo_commands(main.app.state.storage)
    tasks = list(main.app.state.ceo_command_tasks)
    if tasks:
        await asyncio.gather(*tasks)

    recovered = await main._load_ceo_command(main.app.state.storage, recovered_id)
    assert recovered is not None
    assert recovered[0]["status"] == "COMPLETED"
    assert handle.await_count == 2
