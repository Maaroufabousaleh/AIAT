from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import httpx
import pytest

from orchestrator_api.main import _department_for_hiring_text


@pytest.mark.parametrize(
    ("instruction", "team_id"),
    [
        ("Hire a security agent.", "office_cso"),
        ("Hire an infra specialist.", "dept_devops"),
    ],
)
def test_hiring_department_mapping_uses_existing_team_ids(instruction: str, team_id: str):
    assert _department_for_hiring_text(instruction) == team_id


@pytest.mark.anyio
async def test_operator_send_to_ceo_publishes_human_directive(client, monkeypatch):
    published: dict[str, Any] = {}

    class FakeResponse:
        status_code = 200

        @property
        def is_success(self) -> bool:
            return True

        def json(self) -> dict[str, str]:
            return {"entry_id": "stream-entry-1"}

    class FakeAsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def post(self, url: str, json: dict[str, Any]) -> FakeResponse:
            published["url"] = url
            published["envelope"] = json
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    response = await client.post(
        "/ceo/message",
        headers={"Authorization": "Bearer test-mas-key"},
        json={"message": "Hello CEO, confirm you are online."},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "entry_id": "stream-entry-1"}
    assert published["url"].endswith("/messages/publish")

    envelope = published["envelope"]
    assert envelope["msg_type"] == "TASK"
    assert envelope["sender_id"] == "human_operator"
    assert envelope["sender_team"] == "orchestrator"
    assert envelope["recipient_team"] == "exec_ceo"
    assert envelope["project_id"] == "operator-direct"
    assert envelope["payload"] == {
        "action": "HUMAN_DIRECTIVE",
        "instruction": "Hello CEO, confirm you are online.",
        "source": "ceo_chat",
    }


@pytest.mark.anyio
async def test_operator_send_to_ceo_hiring_request_registers_candidate(client, monkeypatch):
    from orchestrator_api.main import app

    published: list[dict[str, Any]] = []

    class FakeResponse:
        status_code = 200
        text = "ok"

        @property
        def is_success(self) -> bool:
            return True

        def json(self) -> dict[str, str]:
            return {"entry_id": f"stream-entry-{len(published)}"}

    class FakeAsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def post(self, url: str, json: dict[str, Any]) -> FakeResponse:
            published.append({"url": url, "envelope": json})
            return FakeResponse()

    worker_id = UUID("00000000-0000-4000-a000-0000000000ce")
    storage = MagicMock()
    storage.register_worker = AsyncMock(
        return_value={
            "id": worker_id,
            "name": "opencode",
            "status": "INACTIVE",
            "adapter_type": "process",
            "adapter_config": {"entrypoint": "WorkerAgent"},
            "sandbox_profile": "restricted",
            "capability_ids": [],
            "team_id": "dept_production",
            "source_repo": "https://github.com/example/opencode",
            "version_pin": None,
            "update_policy": "manual",
            "evaluation_status": "pending",
            "adapter_entrypoint": "WorkerAgent",
        }
    )
    app.state.storage = storage
    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    response = await client.post(
        "/ceo/message",
        headers={"Authorization": "Bearer test-mas-key"},
        json={
            "message": (
                "Create a software engineering department and hire OpenCode "
                "from https://github.com/example/opencode as a software engineer."
            )
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["action"]["status"] == "candidate_registered"
    assert body["action"]["worker"]["id"] == str(worker_id)
    assert body["action"]["worker"]["status"] == "INACTIVE"
    assert body["action"]["worker"]["evaluation_status"] == "pending"

    storage.register_worker.assert_awaited_once()
    _, kwargs = storage.register_worker.await_args
    assert kwargs["name"] == "opencode"
    assert kwargs["source_repo"] == "https://github.com/example/opencode"
    assert kwargs["team_id"] == "dept_production"
    assert kwargs["status"] == "INACTIVE"
    assert kwargs["evaluation_status"] == "pending"
    assert kwargs["sandbox_profile"] == "restricted"

    assert len(published) == 2
    assert published[0]["envelope"]["payload"]["action"] == "HUMAN_DIRECTIVE"
    assert published[0]["envelope"]["payload"]["execution_owner"] == "orchestrator-api"
    assert published[1]["envelope"]["msg_type"] == "RESPONSE"
    assert "Hiring Board ticket" in published[1]["envelope"]["payload"]["response"]


@pytest.mark.anyio
async def test_operator_send_to_ceo_hiring_request_uses_existing_team_ids(client, monkeypatch):
    from orchestrator_api.main import app

    published: list[dict[str, Any]] = []

    class FakeResponse:
        status_code = 200
        text = "ok"

        @property
        def is_success(self) -> bool:
            return True

        def json(self) -> dict[str, str]:
            return {"entry_id": f"stream-entry-{len(published)}"}

    class FakeAsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def post(self, url: str, json: dict[str, Any]) -> FakeResponse:
            published.append({"url": url, "envelope": json})
            return FakeResponse()

    worker_id = UUID("00000000-0000-4000-a000-0000000000cf")
    storage = MagicMock()
    storage.register_worker = AsyncMock(
        return_value={
            "id": worker_id,
            "name": "security_worker",
            "status": "INACTIVE",
            "adapter_type": "process",
            "adapter_config": {"entrypoint": "WorkerAgent"},
            "sandbox_profile": "restricted",
            "capability_ids": [],
            "team_id": "office_cso",
            "source_repo": "https://github.com/example/security-worker",
            "version_pin": None,
            "update_policy": "manual",
            "evaluation_status": "pending",
            "adapter_entrypoint": "WorkerAgent",
        }
    )
    app.state.storage = storage
    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    response = await client.post(
        "/ceo/message",
        headers={"Authorization": "Bearer test-mas-key"},
        json={"message": "Hire a security agent from https://github.com/example/security-worker."},
    )

    assert response.status_code == 200
    _, kwargs = storage.register_worker.await_args
    assert kwargs["team_id"] == "office_cso"


@pytest.mark.anyio
async def test_operator_send_to_ceo_hiring_request_without_repo_asks_for_source(client, monkeypatch):
    published: list[dict[str, Any]] = []

    class FakeResponse:
        status_code = 200
        text = "ok"

        @property
        def is_success(self) -> bool:
            return True

        def json(self) -> dict[str, str]:
            return {"entry_id": "stream-entry"}

    class FakeAsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def post(self, url: str, json: dict[str, Any]) -> FakeResponse:
            published.append({"url": url, "envelope": json})
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    response = await client.post(
        "/ceo/message",
        headers={"Authorization": "Bearer test-mas-key"},
        json={"message": "Hire an agent for QA."},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["action"]["status"] == "needs_source_repo"
    assert len(published) == 2
    assert "need a GitHub repository URL" in published[1]["envelope"]["payload"]["response"]


@pytest.mark.anyio
async def test_operator_send_to_ceo_creates_project_and_traces_workflow(client, monkeypatch):
    from orchestrator_api.main import app

    published: list[dict[str, Any]] = []

    class FakeResponse:
        status_code = 200
        text = "ok"

        @property
        def is_success(self) -> bool:
            return True

        def json(self) -> dict[str, str]:
            return {"entry_id": f"stream-entry-{len(published)}"}

    class FakeAsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def post(self, url: str, json: dict[str, Any]) -> FakeResponse:
            published.append({"url": url, "envelope": json})
            return FakeResponse()

    class FakeController:
        def __init__(self) -> None:
            self.transitions: list[dict[str, Any]] = []

        async def transition(self, **kwargs: Any) -> None:
            self.transitions.append(kwargs)

    project_id = UUID("00000000-0000-4000-a000-00000000c001")
    storage = MagicMock()
    storage.create_project = AsyncMock(
        return_value={
            "id": project_id,
            "name": "CEO Command Project",
            "description": "Build through CEO chat.",
            "state": "INIT",
            "created_by": "human_operator",
        }
    )
    app.state.storage = storage
    app.state.controller = FakeController()
    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    response = await client.post(
        "/ceo/message",
        headers={"Authorization": "Bearer test-mas-key"},
        json={
            "message": (
                "Initialize a new project named 'CEO Command Project' "
                "with description 'Build through CEO chat'."
            )
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["action"]["type"] == "project_create"
    assert body["action"]["project"]["id"] == str(project_id)
    assert "created_project_record" in body["action"]["trace"]
    storage.create_project.assert_awaited_once()
    assert len(published) == 3
    assert published[0]["envelope"]["msg_type"] == "TASK"
    assert published[0]["envelope"]["payload"]["execution_owner"] == "orchestrator-api"
    assert published[1]["envelope"]["msg_type"] == "DIRECTIVE"
    assert published[1]["envelope"]["payload"]["action"] == "START_FEASIBILITY"
    assert published[2]["envelope"]["msg_type"] == "RESPONSE"


@pytest.mark.anyio
async def test_operator_send_to_ceo_reads_company_and_hiring_board(client, monkeypatch):
    from orchestrator_api.main import app

    published: list[dict[str, Any]] = []

    class FakeResponse:
        status_code = 200
        text = "ok"

        @property
        def is_success(self) -> bool:
            return True

        def json(self) -> dict[str, str]:
            return {"entry_id": f"stream-entry-{len(published)}"}

    class FakeAsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def post(self, url: str, json: dict[str, Any]) -> FakeResponse:
            published.append({"url": url, "envelope": json})
            return FakeResponse()

    storage = MagicMock()
    storage.get_config = AsyncMock(
        side_effect=lambda key: {
            "default_company_seeded": "true",
            "default_company_departments": '[{"id":"dept_production","name":"Production"}]',
            "default_company_ceo": '{"id":"ceo_agent","name":"AIAT CEO","role":"CEO"}',
            "default_company_seeded_at": "2026-06-19T00:00:00+00:00",
        }.get(key)
    )
    storage.list_workers = AsyncMock(
        return_value=[
            {
                "id": UUID("00000000-0000-4000-a000-00000000c002"),
                "name": "opencode",
                "status": "INACTIVE",
                "evaluation_status": "pending",
                "source_repo": "https://github.com/example/opencode",
                "team_id": "dept_production",
                "sandbox_profile": "restricted",
            }
        ]
    )
    storage.list_projects = AsyncMock(return_value=[])
    storage.list_capabilities = AsyncMock(return_value=[])
    storage.list_approval_gates = AsyncMock(return_value=[])
    app.state.storage = storage
    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    company_response = await client.post(
        "/ceo/message",
        headers={"Authorization": "Bearer test-mas-key"},
        json={"message": "Show company overview."},
    )
    board_response = await client.post(
        "/ceo/message",
        headers={"Authorization": "Bearer test-mas-key"},
        json={"message": "Show hiring board status."},
    )

    assert company_response.status_code == 200
    assert company_response.json()["action"]["type"] == "company_overview"
    assert "read_company_overview" in company_response.json()["action"]["trace"]
    assert board_response.status_code == 200
    assert board_response.json()["action"]["type"] == "hiring_board"
    assert "opencode" in board_response.json()["action"]["response"]


@pytest.mark.anyio
async def test_operator_send_to_ceo_reads_runtime_readiness(client, monkeypatch):
    published: list[dict[str, Any]] = []

    class FakeResponse:
        status_code = 200
        text = "ok"

        @property
        def is_success(self) -> bool:
            return True

        def json(self) -> dict[str, str]:
            return {"entry_id": f"stream-entry-{len(published)}"}

    class FakeAsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def post(self, url: str, json: dict[str, Any]) -> FakeResponse:
            published.append({"url": url, "envelope": json})
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    response = await client.post(
        "/ceo/message",
        headers={"Authorization": "Bearer test-mas-key"},
        json={"message": "Show LangGraph CrewAI AutoGen Letta runtime readiness."},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["action"]["type"] == "runtime_readiness"
    assert {"langgraph", "crewai", "autogen", "letta"}.issubset(
        {runtime["id"] for runtime in body["action"]["runtimes"]["runtimes"]}
    )
    assert "read_runtime_policy" in body["action"]["trace"]


@pytest.mark.anyio
async def test_operator_send_to_ceo_rejects_missing_auth(client):
    response = await client.post("/ceo/message", json={"message": "hello"})

    assert response.status_code == 401


@pytest.mark.anyio
async def test_operator_send_to_ceo_rejects_legacy_default_credential(client):
    response = await client.post(
        "/ceo/message",
        headers={"Authorization": "Bearer mas-internal"},
        json={"message": "hello"},
    )

    assert response.status_code == 401


@pytest.mark.anyio
async def test_operator_send_to_ceo_fails_closed_without_configured_credentials(
    client, monkeypatch
):
    monkeypatch.delenv("MAS_API_KEY", raising=False)
    monkeypatch.delenv("GATEWAY_API_KEY", raising=False)

    response = await client.post(
        "/ceo/message",
        headers={"Authorization": "Bearer anything"},
        json={"message": "hello"},
    )

    assert response.status_code == 503


@pytest.mark.anyio
async def test_operator_send_to_ceo_rejects_empty_message(client):
    response = await client.post(
        "/ceo/message",
        headers={"Authorization": "Bearer test-mas-key"},
        json={"message": "   "},
    )

    assert response.status_code == 422
    assert "message must not be blank" in response.text
