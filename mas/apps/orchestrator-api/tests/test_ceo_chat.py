from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import httpx
import pytest

from orchestrator_api.main import (
    _department_for_hiring_text,
    _handle_ceo_worker_intent,
    _target_department_for_reclassification_text,
    _worker_name_from_hiring_text,
)


@pytest.mark.parametrize(
    ("instruction", "team_id"),
    [
        ("Hire a security agent.", "office_cso"),
        ("Hire an infra specialist.", "dept_devops"),
        ("Hire a production engineer.", "dept_production"),
    ],
)
def test_hiring_department_mapping_uses_existing_team_ids(instruction: str, team_id: str):
    assert _department_for_hiring_text(instruction) == team_id


def test_reclassification_department_prefers_target_phrase():
    instruction = "reclassify worker qa_bot to devops"

    assert _target_department_for_reclassification_text(instruction) == "dept_devops"


def test_hiring_worker_name_uses_repo_when_role_follows_url():
    instruction = "hire https://github.com/OpenHands/openhands as a software engineer"

    assert (
        _worker_name_from_hiring_text(instruction, "https://github.com/OpenHands/openhands")
        == "openhands"
    )


def test_hiring_worker_name_uses_explicit_name_when_provided():
    instruction = "hire a worker named coding specialist from https://github.com/example/opencode"

    assert (
        _worker_name_from_hiring_text(instruction, "https://github.com/example/opencode")
        == "coding_specialist"
    )


@pytest.mark.anyio
async def test_generic_status_does_not_enter_worker_intent_handler():
    assert await _handle_ceo_worker_intent("what is the status?") is None


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
    storage.get_worker_by_name = AsyncMock(return_value=None)
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
    assert kwargs["isolation_mode"] == "wrapper"
    manifest = kwargs["wrapper_config"]["aiat_manifest"]
    assert manifest["metadata"]["id"] == "opencode"
    assert manifest["metadata"]["source_repo"] == "https://github.com/example/opencode"
    assert manifest["metadata"]["tags"] == [
        "worker",
        "dept_production",
        "ceo_chat_hiring",
    ]
    assert manifest["integration"]["isolation_mode"] == "wrapper"

    assert len(published) == 2
    assert published[0]["envelope"]["payload"]["action"] == "HUMAN_DIRECTIVE"
    assert published[0]["envelope"]["payload"]["execution_owner"] == "orchestrator-api"
    assert published[1]["envelope"]["msg_type"] == "RESPONSE"
    assert "Hiring Board ticket" in published[1]["envelope"]["payload"]["response"]
    assert "Routing reason:" in published[1]["envelope"]["payload"]["response"]


@pytest.mark.anyio
async def test_operator_send_to_ceo_hiring_url_then_role_uses_repo_name(client, monkeypatch):
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

    worker_id = UUID("00000000-0000-4000-a000-0000000000d0")
    storage = MagicMock()
    storage.get_worker_by_name = AsyncMock(return_value=None)
    storage.register_worker = AsyncMock(
        return_value={
            "id": worker_id,
            "name": "openhands",
            "status": "INACTIVE",
            "adapter_type": "process",
            "adapter_config": {"entrypoint": "WorkerAgent"},
            "sandbox_profile": "restricted",
            "capability_ids": [],
            "team_id": "dept_production",
            "source_repo": "https://github.com/OpenHands/openhands",
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
        json={"message": "hire https://github.com/OpenHands/openhands as a software engineer"},
    )

    assert response.status_code == 200
    _, kwargs = storage.register_worker.await_args
    assert kwargs["name"] == "openhands"
    assert kwargs["team_id"] == "dept_production"
    assert "software engineering" in response.json()["action"]["response"]


@pytest.mark.anyio
async def test_operator_send_to_ceo_hiring_same_repo_reuses_existing_candidate(
    client, monkeypatch
):
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

    existing_worker = {
        "id": UUID("00000000-0000-4000-a000-0000000000f4"),
        "name": "openhands",
        "status": "INACTIVE",
        "adapter_type": "process",
        "adapter_config": {"entrypoint": "WorkerAgent"},
        "sandbox_profile": "restricted",
        "capability_ids": [],
        "team_id": "dept_production",
        "source_repo": "https://github.com/OpenHands/openhands",
        "version_pin": None,
        "update_policy": "manual",
        "evaluation_status": "pending",
        "adapter_entrypoint": "WorkerAgent",
    }
    storage = MagicMock()
    storage.get_worker_by_name = AsyncMock(return_value=existing_worker)
    storage.register_worker = AsyncMock(return_value={})
    app.state.storage = storage
    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    response = await client.post(
        "/ceo/message",
        headers={"Authorization": "Bearer test-mas-key"},
        json={"message": "hire https://github.com/OpenHands/openhands as a software engineer"},
    )

    assert response.status_code == 200
    action = response.json()["action"]
    assert action["type"] == "worker_hiring"
    assert action["status"] == "existing_candidate"
    assert action["worker"]["id"] == str(existing_worker["id"])
    assert "did not create or reset a duplicate" in action["response"]
    storage.register_worker.assert_not_awaited()


@pytest.mark.anyio
async def test_operator_send_to_ceo_hiring_name_conflict_blocks_overwrite(
    client, monkeypatch
):
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

    existing_worker = {
        "id": UUID("00000000-0000-4000-a000-0000000000f5"),
        "name": "coding_specialist",
        "status": "INACTIVE",
        "evaluation_status": "pending",
        "source_repo": "https://github.com/example/original-worker",
        "team_id": "dept_production",
    }
    storage = MagicMock()
    storage.get_worker_by_name = AsyncMock(return_value=existing_worker)
    storage.register_worker = AsyncMock(return_value={})
    app.state.storage = storage
    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    response = await client.post(
        "/ceo/message",
        headers={"Authorization": "Bearer test-mas-key"},
        json={
            "message": (
                "hire a worker named coding specialist from "
                "https://github.com/example/different-worker"
            )
        },
    )

    assert response.status_code == 200
    action = response.json()["action"]
    assert action["type"] == "worker_hiring"
    assert action["status"] == "name_conflict"
    assert "will not overwrite" in action["response"]
    assert "explicit unique name" in action["response"]
    storage.register_worker.assert_not_awaited()


@pytest.mark.anyio
async def test_operator_send_to_ceo_explains_production_department_routing(client, monkeypatch):
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

    worker_id = UUID("00000000-0000-4000-a000-0000000000d1")
    storage = MagicMock()
    worker = {
        "id": worker_id,
        "name": "openhands",
        "status": "INACTIVE",
        "evaluation_status": "pending",
        "source_repo": "https://github.com/OpenHands/openhands",
        "team_id": "dept_production",
        "sandbox_profile": "restricted",
        "updated_at": "2026-07-05T20:00:00+00:00",
    }
    storage.get_worker = AsyncMock(return_value=worker)
    storage.list_workers = AsyncMock(
        return_value=[
            worker,
            {
                **worker,
                "id": UUID("00000000-0000-4000-a000-0000000000d0"),
                "name": "another_engineer",
                "source_repo": "https://github.com/example/another-engineer",
            },
        ]
    )
    app.state.storage = storage
    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    response = await client.post(
        "/ceo/message",
        headers={"Authorization": "Bearer test-mas-key"},
        json={"message": "why production dep?", "context_worker_id": str(worker_id)},
    )

    assert response.status_code == 200
    action = response.json()["action"]
    assert action["type"] == "hiring_department_explanation"
    assert action["status"] == "explained"
    assert "`openhands` was routed to `dept_production`" in action["response"]
    assert "implementation workers" in action["response"]
    storage.get_worker.assert_awaited_once_with(worker_id)
    assert published[-1]["envelope"]["payload"]["context"]["worker_id"] == str(worker_id)


@pytest.mark.anyio
async def test_operator_send_to_ceo_does_not_guess_ambiguous_hiring_followup(client, monkeypatch):
    from orchestrator_api.main import app

    class FakeResponse:
        status_code = 200
        text = "ok"
        is_success = True

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
            return FakeResponse()

    storage = MagicMock()
    storage.list_workers = AsyncMock(
        return_value=[
            {
                "id": UUID("00000000-0000-4000-a000-0000000000d7"),
                "name": "candidate_one",
                "source_repo": "https://github.com/example/one",
                "team_id": "dept_production",
            },
            {
                "id": UUID("00000000-0000-4000-a000-0000000000d8"),
                "name": "candidate_two",
                "source_repo": "https://github.com/example/two",
                "team_id": "dept_production",
            },
        ]
    )
    app.state.storage = storage
    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    response = await client.post(
        "/ceo/message",
        headers={"Authorization": "Bearer test-mas-key"},
        json={"message": "why production dep?"},
    )

    assert response.status_code == 200
    action = response.json()["action"]
    assert action["worker"] is None
    assert "will not guess" in action["response"]
    assert "worker <name>" in action["response"]


@pytest.mark.anyio
async def test_operator_send_to_ceo_uses_context_for_worker_pronoun(client, monkeypatch):
    from orchestrator_api.main import app

    class FakeResponse:
        status_code = 200
        text = "ok"
        is_success = True

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
            return FakeResponse()

    worker_id = UUID("00000000-0000-4000-a000-0000000000d9")
    worker = {
        "id": worker_id,
        "name": "context_candidate",
        "status": "INACTIVE",
        "evaluation_status": "pending",
        "source_repo": "https://github.com/example/context-candidate",
        "team_id": "dept_qa",
        "sandbox_profile": "restricted",
    }
    storage = MagicMock()
    storage.list_workers = AsyncMock(return_value=[])
    storage.get_worker = AsyncMock(return_value=worker)
    storage.get_evaluation_reports = AsyncMock(return_value=[])
    app.state.storage = storage
    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    response = await client.post(
        "/ceo/message",
        headers={"Authorization": "Bearer test-mas-key"},
        json={"message": "what is its status?", "context_worker_id": str(worker_id)},
    )

    assert response.status_code == 200
    action = response.json()["action"]
    assert action["type"] == "worker_status"
    assert action["worker"]["id"] == str(worker_id)
    assert "context_candidate" in action["response"]


@pytest.mark.anyio
async def test_operator_send_to_ceo_controls_named_worker_lifecycle(client, monkeypatch):
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

    worker_id = UUID("00000000-0000-4000-a000-0000000000d2")
    worker = {
        "id": worker_id,
        "name": "openhands",
        "status": "INACTIVE",
        "evaluation_status": "pending",
        "source_repo": "https://github.com/OpenHands/openhands",
        "team_id": "dept_production",
        "sandbox_profile": "restricted",
        "capability_ids": [],
    }
    approved_worker = {**worker, "evaluation_status": "approved"}
    active_worker = {**approved_worker, "status": "ACTIVE"}

    storage = MagicMock()
    storage.list_workers = AsyncMock(return_value=[worker])
    storage.get_evaluation_reports = AsyncMock(
        return_value=[
            {
                "id": UUID("00000000-0000-4000-a000-0000000000e2"),
                "worker_id": worker_id,
                "verdict": "APPROVED",
                "overall_score": 87.2,
                "blocked_reasons": [],
                "recommended_status": "ACTIVE",
                "requires_human_approval": False,
            }
        ]
    )
    storage.update_worker_config = AsyncMock(return_value=None)
    storage.update_worker_status = AsyncMock(return_value=None)
    storage.get_worker = AsyncMock(side_effect=[approved_worker, approved_worker, active_worker])
    app.state.storage = storage
    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    status_response = await client.post(
        "/ceo/message",
        headers={"Authorization": "Bearer test-mas-key"},
        json={"message": "status of worker openhands"},
    )
    approve_response = await client.post(
        "/ceo/message",
        headers={"Authorization": "Bearer test-mas-key"},
        json={"message": "approve worker openhands"},
    )
    activate_response = await client.post(
        "/ceo/message",
        headers={"Authorization": "Bearer test-mas-key"},
        json={"message": "activate worker openhands"},
    )

    assert status_response.status_code == 200
    assert status_response.json()["action"]["type"] == "worker_status"
    assert "Latest verdict `APPROVED`" in status_response.json()["action"]["response"]

    assert approve_response.status_code == 200
    assert approve_response.json()["action"]["type"] == "worker_approval"
    assert approve_response.json()["action"]["status"] == "approved"
    storage.update_worker_config.assert_awaited_once_with(worker_id, evaluation_status="approved")

    assert activate_response.status_code == 200
    assert activate_response.json()["action"]["type"] == "worker_status_transition"
    storage.update_worker_status.assert_awaited_once_with(worker_id, status="ACTIVE")


@pytest.mark.anyio
async def test_operator_send_to_ceo_blocks_approval_when_latest_evaluation_rejected(
    client, monkeypatch
):
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

    worker_id = UUID("00000000-0000-4000-a000-0000000000d3")
    worker = {
        "id": worker_id,
        "name": "openhands",
        "status": "INACTIVE",
        "evaluation_status": "rejected",
        "source_repo": "https://github.com/OpenHands/openhands",
        "team_id": "dept_production",
        "sandbox_profile": "restricted",
        "capability_ids": [],
    }
    storage = MagicMock()
    storage.list_workers = AsyncMock(return_value=[worker])
    storage.get_evaluation_reports = AsyncMock(
        return_value=[
            {
                "id": UUID("00000000-0000-4000-a000-0000000000e3"),
                "worker_id": worker_id,
                "verdict": "REJECTED",
                "overall_score": 72.7,
                "blocked_reasons": ["manifest_validation: No AIAT worker manifest found"],
                "recommended_status": "REJECTED",
                "requires_human_approval": False,
            }
        ]
    )
    storage.update_worker_config = AsyncMock(return_value=None)
    app.state.storage = storage
    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    response = await client.post(
        "/ceo/message",
        headers={"Authorization": "Bearer test-mas-key"},
        json={"message": "approve worker openhands"},
    )

    assert response.status_code == 200
    action = response.json()["action"]
    assert action["type"] == "worker_approval"
    assert action["status"] == "blocked"
    assert "No AIAT worker manifest found" in action["response"]
    storage.update_worker_config.assert_not_awaited()


@pytest.mark.anyio
async def test_operator_send_to_ceo_requires_evaluation_before_approving_external_worker(
    client, monkeypatch
):
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

    worker_id = UUID("00000000-0000-4000-a000-0000000000d4")
    worker = {
        "id": worker_id,
        "name": "unevaluated_worker",
        "status": "INACTIVE",
        "evaluation_status": "pending",
        "source_repo": "https://github.com/example/unevaluated-worker",
        "team_id": "dept_production",
        "sandbox_profile": "restricted",
    }
    storage = MagicMock()
    storage.list_workers = AsyncMock(return_value=[worker])
    storage.get_evaluation_reports = AsyncMock(return_value=[])
    storage.update_worker_config = AsyncMock(return_value=None)
    app.state.storage = storage
    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    response = await client.post(
        "/ceo/message",
        headers={"Authorization": "Bearer test-mas-key"},
        json={"message": "approve unevaluated_worker"},
    )

    assert response.status_code == 200
    action = response.json()["action"]
    assert action["type"] == "worker_approval"
    assert action["status"] == "needs_evaluation"
    assert "must have a stored evaluation report" in action["response"]
    storage.update_worker_config.assert_not_awaited()


@pytest.mark.anyio
async def test_operator_send_to_ceo_rejects_inactive_candidate(client, monkeypatch):
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

    worker_id = UUID("00000000-0000-4000-a000-0000000000d5")
    worker = {
        "id": worker_id,
        "name": "rejected_candidate",
        "status": "INACTIVE",
        "evaluation_status": "pending",
        "source_repo": "https://github.com/example/rejected-candidate",
        "team_id": "dept_production",
        "sandbox_profile": "restricted",
    }
    rejected_worker = {**worker, "evaluation_status": "rejected"}
    storage = MagicMock()
    storage.list_workers = AsyncMock(return_value=[worker])
    storage.update_worker_config = AsyncMock(return_value=None)
    storage.update_worker_status = AsyncMock(return_value=None)
    storage.get_worker = AsyncMock(return_value=rejected_worker)
    app.state.storage = storage
    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    response = await client.post(
        "/ceo/message",
        headers={"Authorization": "Bearer test-mas-key"},
        json={"message": "reject worker rejected_candidate"},
    )

    assert response.status_code == 200
    action = response.json()["action"]
    assert action["type"] == "worker_rejection"
    assert action["status"] == "rejected"
    assert action["worker"]["evaluation_status"] == "rejected"
    assert "kept inactive" in action["response"]
    storage.update_worker_config.assert_awaited_once_with(worker_id, evaluation_status="rejected")
    storage.update_worker_status.assert_not_awaited()


@pytest.mark.anyio
async def test_operator_send_to_ceo_blocks_rejecting_active_worker(client, monkeypatch):
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

    worker_id = UUID("00000000-0000-4000-a000-0000000000d6")
    worker = {
        "id": worker_id,
        "name": "active_worker",
        "status": "ACTIVE",
        "evaluation_status": "approved",
        "source_repo": "https://github.com/example/active-worker",
        "team_id": "dept_production",
        "sandbox_profile": "restricted",
    }
    storage = MagicMock()
    storage.list_workers = AsyncMock(return_value=[worker])
    storage.update_worker_config = AsyncMock(return_value=None)
    storage.update_worker_status = AsyncMock(return_value=None)
    app.state.storage = storage
    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    response = await client.post(
        "/ceo/message",
        headers={"Authorization": "Bearer test-mas-key"},
        json={"message": "deny worker active_worker"},
    )

    assert response.status_code == 200
    action = response.json()["action"]
    assert action["type"] == "worker_rejection"
    assert action["status"] == "needs_deactivation"
    assert "Drain or deactivate it first" in action["response"]
    storage.update_worker_config.assert_not_awaited()
    storage.update_worker_status.assert_not_awaited()


@pytest.mark.anyio
async def test_operator_send_to_ceo_prefers_exact_worker_name_over_substring(client, monkeypatch):
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

    target_id = UUID("00000000-0000-4000-a000-0000000000f2")
    storage = MagicMock()
    storage.list_workers = AsyncMock(
        return_value=[
            {
                "id": UUID("00000000-0000-4000-a000-0000000000f1"),
                "name": "ceo",
                "status": "ACTIVE",
                "evaluation_status": "approved",
                "team_id": "exec_ceo",
            },
            {
                "id": target_id,
                "name": "ceo_chat_smoke_1783282647840",
                "status": "INACTIVE",
                "evaluation_status": "pending",
                "source_repo": "https://github.com/example/ceo-chat-smoke-1783282647840",
                "team_id": "dept_production",
                "sandbox_profile": "restricted",
            },
        ]
    )
    storage.get_evaluation_reports = AsyncMock(return_value=[])
    app.state.storage = storage
    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    response = await client.post(
        "/ceo/message",
        headers={"Authorization": "Bearer test-mas-key"},
        json={"message": "status of worker ceo_chat_smoke_1783282647840"},
    )

    assert response.status_code == 200
    action = response.json()["action"]
    assert action["type"] == "worker_status"
    assert action["worker"]["id"] == str(target_id)


@pytest.mark.anyio
async def test_operator_send_to_ceo_reclassifies_candidate_department(client, monkeypatch):
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

    worker_id = UUID("00000000-0000-4000-a000-0000000000f3")
    worker = {
        "id": worker_id,
        "name": "openhands",
        "status": "INACTIVE",
        "evaluation_status": "approved",
        "source_repo": "https://github.com/OpenHands/openhands",
        "team_id": "dept_production",
        "sandbox_profile": "restricted",
    }
    updated_worker = {**worker, "team_id": "dept_qa", "evaluation_status": "pending"}
    storage = MagicMock()
    storage.list_workers = AsyncMock(return_value=[worker])
    storage.update_worker_config = AsyncMock(return_value=None)
    storage.get_worker = AsyncMock(return_value=updated_worker)
    app.state.storage = storage
    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    response = await client.post(
        "/ceo/message",
        headers={"Authorization": "Bearer test-mas-key"},
        json={"message": "reclassify openhands to QA"},
    )

    assert response.status_code == 200
    action = response.json()["action"]
    assert action["type"] == "worker_reclassification"
    assert action["status"] == "reclassified"
    assert action["previous_team_id"] == "dept_production"
    assert action["team_id"] == "dept_qa"
    assert action["worker"]["team_id"] == "dept_qa"
    assert action["worker"]["evaluation_status"] == "pending"
    assert "QA owns test automation" in action["response"]
    assert "reset evaluation status to `pending`" in action["response"]
    storage.update_worker_config.assert_awaited_once_with(
        worker_id, team_id="dept_qa", evaluation_status="pending"
    )


@pytest.mark.anyio
async def test_operator_send_to_ceo_blocks_reclassifying_active_worker(client, monkeypatch):
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

    worker_id = UUID("00000000-0000-4000-a000-0000000000f6")
    worker = {
        "id": worker_id,
        "name": "active_opencode",
        "status": "ACTIVE",
        "evaluation_status": "approved",
        "source_repo": "https://github.com/example/active-opencode",
        "team_id": "dept_production",
        "sandbox_profile": "restricted",
    }
    storage = MagicMock()
    storage.list_workers = AsyncMock(return_value=[worker])
    storage.update_worker_config = AsyncMock(return_value=None)
    storage.get_worker = AsyncMock(return_value=worker)
    app.state.storage = storage
    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    response = await client.post(
        "/ceo/message",
        headers={"Authorization": "Bearer test-mas-key"},
        json={"message": "reclassify active_opencode to QA"},
    )

    assert response.status_code == 200
    action = response.json()["action"]
    assert action["type"] == "worker_reclassification"
    assert action["status"] == "needs_deactivation"
    assert action["previous_team_id"] == "dept_production"
    assert action["team_id"] == "dept_qa"
    assert "Drain or deactivate it first" in action["response"]
    storage.update_worker_config.assert_not_awaited()


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
    storage.get_worker_by_name = AsyncMock(return_value=None)
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
    storage.get_project = AsyncMock(return_value=None)
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
