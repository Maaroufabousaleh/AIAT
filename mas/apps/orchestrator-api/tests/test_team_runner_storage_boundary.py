"""Tests for the control-plane-only team-runner storage boundary."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest


@pytest.mark.anyio
async def test_team_runner_storage_requires_matching_team_identity(client, monkeypatch) -> None:
    from orchestrator_api import main

    monkeypatch.setenv("AIAT_WORKER_API_KEY", "test-worker-key")
    previous = main.app.state.storage
    main.app.state.storage = object()
    try:
        response = await client.post(
            "/internal/team-runners/office_cio/storage",
            headers={
                "X-API-Key": "test-worker-key",
                "X-AIAT-Team-ID": "office_cfo",
            },
            json={"operation": "document_get", "payload": {"document_id": str(uuid4())}},
        )
        assert response.status_code == 403
    finally:
        main.app.state.storage = previous


@pytest.mark.anyio
async def test_team_runner_storage_uses_allowlisted_document_operation(client, monkeypatch) -> None:
    from orchestrator_api import main

    monkeypatch.setenv("AIAT_WORKER_API_KEY", "test-worker-key")
    document_id = uuid4()
    project_id = uuid4()

    class Storage:
        engine = object()

        async def get_document(self, value: UUID) -> dict[str, object]:
            assert value == document_id
            return {"id": value, "project_id": project_id, "status": "DRAFT"}

    previous = main.app.state.storage
    main.app.state.storage = Storage()
    try:
        response = await client.post(
            "/internal/team-runners/office_cio/storage",
            headers={
                "X-API-Key": "test-worker-key",
                "X-AIAT-Team-ID": "office_cio",
            },
            json={"operation": "document_get", "payload": {"document_id": str(document_id)}},
        )
        assert response.status_code == 200
        assert response.json() == {
            "id": str(document_id),
            "project_id": str(project_id),
            "status": "DRAFT",
        }
    finally:
        main.app.state.storage = previous


@pytest.mark.anyio
async def test_team_runner_storage_health_is_a_durable_startup_probe(client, monkeypatch) -> None:
    from orchestrator_api import main

    monkeypatch.setenv("AIAT_WORKER_API_KEY", "test-worker-key")

    class Storage:
        engine = object()

        async def get_config(self, key: str) -> str:
            assert key == "system_state"
            return "RUNNING"

    previous = main.app.state.storage
    main.app.state.storage = Storage()
    try:
        response = await client.post(
            "/internal/team-runners/office_cio/storage",
            headers={
                "X-API-Key": "test-worker-key",
                "X-AIAT-Team-ID": "office_cio",
            },
            json={"operation": "storage_health", "payload": {}},
        )
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
    finally:
        main.app.state.storage = previous


@pytest.mark.anyio
async def test_team_runner_storage_rejects_operator_and_unknown_operations(client, monkeypatch) -> None:
    monkeypatch.setenv("AIAT_WORKER_API_KEY", "test-worker-key")
    denied = await client.post(
        "/internal/team-runners/office_cio/storage",
        headers={
            "X-API-Key": "test-operator-key",
            "X-AIAT-Team-ID": "office_cio",
        },
        json={"operation": "document_get", "payload": {"document_id": str(uuid4())}},
    )
    assert denied.status_code == 403

    # The operation Literal is validated before the handler and cannot turn
    # this route into a generic arbitrary-storage endpoint.
    unknown = await client.post(
        "/internal/team-runners/office_cio/storage",
        headers={
            "X-API-Key": "test-worker-key",
            "X-AIAT-Team-ID": "office_cio",
        },
        json={"operation": "sql", "payload": {}},
    )
    assert unknown.status_code == 422


@pytest.mark.anyio
async def test_team_runner_storage_scopes_model_snapshot_to_project(client, monkeypatch) -> None:
    from orchestrator_api import main

    monkeypatch.setenv("AIAT_WORKER_API_KEY", "test-worker-key")
    snapshot_id = uuid4()
    allowed_project_id = uuid4()
    denied_project_id = uuid4()

    class Storage:
        engine = object()

        async def get_model_resolution_snapshot(
            self,
            value: UUID,
            *,
            project_id: UUID | None = None,
        ) -> dict[str, object] | None:
            assert value == snapshot_id
            assert project_id in {allowed_project_id, denied_project_id}
            return {
                "id": snapshot_id,
                "project_id": allowed_project_id,
                "resolved_profile_id": "governance",
                "resolved_profile_version": "v1",
                "provider_id": "provider",
                "exact_model_id": "provider/model",
            }

    previous = main.app.state.storage
    main.app.state.storage = Storage()
    try:
        allowed = await client.post(
            "/internal/team-runners/office_cio/storage",
            headers={
                "X-API-Key": "test-worker-key",
                "X-AIAT-Team-ID": "office_cio",
            },
            json={
                "operation": "model_resolution_snapshot_get",
                "payload": {
                    "snapshot_id": str(snapshot_id),
                    "project_id": str(allowed_project_id),
                },
            },
        )
        denied = await client.post(
            "/internal/team-runners/office_cio/storage",
            headers={
                "X-API-Key": "test-worker-key",
                "X-AIAT-Team-ID": "office_cio",
            },
            json={
                "operation": "model_resolution_snapshot_get",
                "payload": {
                    "snapshot_id": str(snapshot_id),
                    "project_id": str(denied_project_id),
                },
            },
        )
    finally:
        main.app.state.storage = previous

    assert allowed.status_code == 200
    assert allowed.json()["exact_model_id"] == "provider/model"
    assert denied.status_code == 200
    assert denied.json() is None
