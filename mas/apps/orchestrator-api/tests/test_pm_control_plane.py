from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import ANY, AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest


@pytest.mark.anyio
async def test_unknown_provider_webhook_becomes_conflict(client):
    from orchestrator_api.main import _apply_normalized_command

    from mas_core.integrations.contracts import NormalizedCommand, ObjectType

    connection_id = uuid4()
    inbox_id = uuid4()
    storage = MagicMock()
    storage.get_pm_mapping = AsyncMock(return_value=None)
    storage.create_pm_conflict = AsyncMock(return_value={"id": uuid4()})
    storage.mark_pm_inbox_event = AsyncMock()
    command = NormalizedCommand(
        connection_id=connection_id,
        object_type=ObjectType.WORK_ITEM,
        external_id="external-404",
        operation="update",
        fields={"title": "unmapped"},
        idempotency_key=f"{connection_id}:delivery-1",
    )

    result = await _apply_normalized_command(storage, command, {"id": inbox_id})

    assert result == {"status": "conflict", "reason": "unknown_mapping", "external_id": "external-404"}
    storage.create_pm_conflict.assert_awaited_once()
    assert storage.create_pm_conflict.await_args.kwargs["reason"] == "unknown_mapping"
    storage.mark_pm_inbox_event.assert_awaited_once_with(
        inbox_id,
        status="CONFLICT",
        error="external object is not mapped",
    )


@pytest.mark.anyio
async def test_provider_command_uses_cas_and_skips_origin_projection(client):
    from orchestrator_api.main import _apply_normalized_command

    from mas_core.integrations.contracts import NormalizedCommand, ObjectType

    connection_id = uuid4()
    issue_id = uuid4()
    inbox_id = uuid4()
    project_id = uuid4()
    mapping = {"aiat_object_id": issue_id, "external_id": "42", "provider_version": "v1", "exported_revision": 3}
    issue = {
        "id": issue_id,
        "project_id": project_id,
        "title": "old",
        "description": "body",
        "status": "backlog",
        "priority": "medium",
        "revision": 3,
    }
    refreshed = {**issue, "status": "in_progress", "revision": 4}
    storage = MagicMock()
    storage.get_pm_mapping = AsyncMock(return_value=mapping)
    storage.get_issue = AsyncMock(side_effect=[issue, refreshed])
    storage.list_pm_bindings = AsyncMock(
        return_value=[
            {
                "id": uuid4(),
                "project_id": project_id,
                "connection_id": connection_id,
                "direction": "inbound",
                "status": "ACTIVE",
            }
        ]
    )
    storage.get_pm_connection = AsyncMock(return_value={"id": connection_id, "provider_kind": "fake", "base_url": "https://fake.example", "status": "ACTIVE", "config": {}})
    storage.get_pm_external_actor_mapping = AsyncMock(return_value={"id": uuid4(), "status": "TRUSTED", "aiat_identity_id": "operator", "authorized_scopes": ["issue.priority"]})
    storage.update_issue = AsyncMock()
    storage.upsert_pm_mapping = AsyncMock()
    storage.enqueue_pm_outbox = AsyncMock(return_value={"id": uuid4(), "status": "PENDING"})
    storage.mark_pm_inbox_event = AsyncMock()
    storage.create_pm_conflict = AsyncMock()
    command = NormalizedCommand(
        connection_id=connection_id,
        object_type=ObjectType.WORK_ITEM,
        external_id="42",
        operation="update",
        fields={"status": "open"},
        expected_canonical_revision=3,
        actor={"actor_id": "human-admin", "immutable_actor_id": True, "role": "human"},
        idempotency_key=f"{connection_id}:delivery-2",
    )

    result = await _apply_normalized_command(storage, command, {"id": inbox_id})

    assert result["status"] == "applied"
    storage.update_issue.assert_awaited_once_with(
        issue_id,
        expected_revision=3,
        status="in_progress",
    )
    storage.mark_pm_inbox_event.assert_awaited_once_with(inbox_id, status="PROCESSED")
    storage.enqueue_pm_outbox.assert_not_awaited()


@pytest.mark.anyio
async def test_provider_marker_echo_does_not_overwrite_canonical_description(client):
    from orchestrator_api.main import _apply_normalized_command

    from mas_core.integrations.contracts import NormalizedCommand, ObjectType

    connection_id = uuid4()
    issue_id = uuid4()
    project_id = uuid4()
    storage = MagicMock()
    storage.get_pm_mapping = AsyncMock(return_value={"aiat_object_id": issue_id, "external_id": "42", "provider_version": "1"})
    storage.get_issue = AsyncMock(return_value={"id": issue_id, "project_id": project_id, "title": "canonical", "description": "source", "revision": 2})
    storage.list_pm_bindings = AsyncMock(return_value=[{"id": uuid4(), "project_id": project_id, "connection_id": connection_id, "direction": "inbound", "status": "ACTIVE"}])
    storage.get_pm_connection = AsyncMock(return_value={"id": connection_id, "provider_kind": "github", "status": "ACTIVE", "config": {"repository": "acme/app"}})
    storage.upsert_pm_mapping = AsyncMock()
    storage.mark_pm_inbox_event = AsyncMock()
    storage.create_pm_conflict = AsyncMock()
    command = NormalizedCommand(
        connection_id=connection_id,
        object_type=ObjectType.WORK_ITEM,
        external_id="42",
        operation="update",
        external_repository="acme/app",
        fields={
            "title": "canonical",
            "description": f"<!-- aiat:object={issue_id};revision=2 -->\n\nprovider copy",
            "_aiat_marker_object_id": str(issue_id),
            "_aiat_marker_revision": 2,
        },
        idempotency_key="echo-1",
    )
    result = await _apply_normalized_command(storage, command, {"id": uuid4()})
    assert result["status"] == "echo"
    storage.update_issue.assert_not_called()
    storage.upsert_pm_mapping.assert_awaited_once()


@pytest.mark.anyio
async def test_read_only_youtrack_revision_marker_echo_is_evidence_only(client):
    from orchestrator_api.main import _apply_normalized_command

    from mas_core.integrations.contracts import NormalizedCommand, ObjectType

    connection_id = uuid4()
    issue_id = uuid4()
    project_id = uuid4()
    storage = MagicMock()
    storage.get_pm_mapping = AsyncMock(return_value={"aiat_object_id": issue_id, "external_id": "3-23", "provider_version": "42"})
    storage.get_issue = AsyncMock(
        return_value={
            "id": issue_id,
            "project_id": project_id,
            "title": "canonical",
            "description": "projected",
            "status": "backlog",
            "priority": "medium",
            "revision": 2,
        }
    )
    binding_id = uuid4()
    storage.list_pm_bindings = AsyncMock(
        return_value=[
            {
                "id": binding_id,
                "project_id": project_id,
                "connection_id": connection_id,
                "direction": "both",
                "status": "READ_ONLY",
            }
        ]
    )
    storage.get_pm_connection = AsyncMock(return_value={"id": connection_id, "provider_kind": "youtrack", "status": "SHADOW", "config": {}})
    storage.upsert_pm_mapping = AsyncMock()
    storage.mark_pm_inbox_event = AsyncMock()
    storage.create_pm_conflict = AsyncMock()
    command = NormalizedCommand(
        connection_id=connection_id,
        object_type=ObjectType.WORK_ITEM,
        external_id="3-23",
        operation="update",
        fields={
            "title": "canonical",
            "description": "projected",
            "status": "backlog",
            "priority": "medium",
            "_aiat_marker_revision": 2,
        },
        idempotency_key="read-only-echo",
    )

    result = await _apply_normalized_command(storage, command, {"id": uuid4()})

    assert result["status"] == "echo"
    storage.create_pm_conflict.assert_not_awaited()
    storage.mark_pm_inbox_event.assert_awaited_once_with(ANY, status="PROCESSED", error=None)
    storage.upsert_pm_mapping.assert_awaited_once()


@pytest.mark.anyio
async def test_read_only_provider_comment_is_evidence_only(client):
    from orchestrator_api.main import _apply_normalized_command

    from mas_core.integrations.contracts import NormalizedCommand, ObjectType

    connection_id = uuid4()
    issue_id = uuid4()
    project_id = uuid4()
    storage = MagicMock()
    storage.get_pm_mapping = AsyncMock(return_value={"aiat_object_id": issue_id, "external_id": "3-23"})
    storage.get_issue = AsyncMock(return_value={"id": issue_id, "project_id": project_id, "revision": 2})
    storage.list_pm_bindings = AsyncMock(
        return_value=[
            {
                "id": uuid4(),
                "project_id": project_id,
                "connection_id": connection_id,
                "direction": "both",
                "status": "READ_ONLY",
            }
        ]
    )
    storage.get_pm_connection = AsyncMock(return_value={"id": connection_id, "provider_kind": "youtrack", "status": "SHADOW", "config": {}})
    storage.upsert_pm_mapping = AsyncMock()
    storage.create_pm_conflict = AsyncMock()
    storage.mark_pm_inbox_event = AsyncMock()
    storage.create_work_item_comment = AsyncMock()
    storage.create_work_item_comment_with_pm_projections = AsyncMock()
    command = NormalizedCommand(
        connection_id=connection_id,
        object_type=ObjectType.WORK_ITEM,
        external_id="3-23",
        operation="comment",
        fields={"comment": "READ_ONLY human inbound certification 20260730"},
        idempotency_key="read-only-comment",
    )

    result = await _apply_normalized_command(storage, command, {"id": uuid4()})

    assert result == {"status": "conflict", "reason": "out_of_scope"}
    storage.create_pm_conflict.assert_awaited_once()
    storage.create_work_item_comment.assert_not_awaited()
    storage.create_work_item_comment_with_pm_projections.assert_not_awaited()


@pytest.mark.anyio
async def test_source_control_webhook_is_recorded_as_evidence(client):
    from orchestrator_api.main import _apply_normalized_command

    from mas_core.integrations.contracts import NormalizedCommand, ObjectType

    connection_id = uuid4()
    storage = MagicMock()
    storage.get_pm_connection = AsyncMock(return_value={"status": "ACTIVE", "config": {"repository": "acme/app"}})
    storage.record_integration_evidence = AsyncMock(return_value={"id": uuid4()})
    storage.mark_pm_inbox_event = AsyncMock()
    command = NormalizedCommand(
        connection_id=connection_id,
        object_type=ObjectType.CHECK,
        external_id="check-1",
        operation="completed",
        external_repository="acme/app",
        fields={"conclusion": "success"},
        idempotency_key="check-1",
    )
    result = await _apply_normalized_command(storage, command, {"id": uuid4()})
    assert result["status"] == "evidence_recorded"
    storage.record_integration_evidence.assert_awaited_once()


@pytest.mark.anyio
async def test_disabled_source_control_connection_cannot_mutate(client):
    from fastapi import HTTPException
    from orchestrator_api.main import _require_source_control_capability

    with pytest.raises(HTTPException) as exc_info:
        await _require_source_control_capability(
            {"status": "DISABLED", "provider_kind": "github", "id": uuid4()},
            "repositories",
            write=True,
        )
    assert exc_info.value.status_code == 409


def test_provider_failure_classification_preserves_retryable_conflicts():
    from orchestrator_api.main import _provider_failure_is_permanent

    from mas_core.integrations.providers.base import ProviderRequestError

    assert _provider_failure_is_permanent(ProviderRequestError("POST", "/issues", 400, "bad request"))
    assert not _provider_failure_is_permanent(ProviderRequestError("POST", "/issues", 409, "conflict"))
    assert not _provider_failure_is_permanent(ProviderRequestError("POST", "/issues", 429, "rate limited"))


@pytest.mark.anyio
async def test_github_broker_rejects_repository_path_injection(client):
    from orchestrator_api.main import _integration_run_token_broker

    from mas_core.integrations.contracts import ProviderConnection

    connection = ProviderConnection(
        provider_kind="github",
        display_name="GitHub",
        base_url="https://api.github.com",
        credential_ref="installation-token",
        capability_profile="delivery",
        config={
            "github_app_id": "123",
            "github_installation_id": "77",
            "github_app_private_key_ref": "github-app-private-key",
        },
    )
    with pytest.raises(ValueError):
        await _integration_run_token_broker(connection, "acme/app?redirect=/evil", {"contents": "write"})

    with pytest.raises(ValueError):
        await _integration_run_token_broker(connection, "acme/app", {"administration": "write"})


@pytest.mark.anyio
async def test_webhook_persists_raw_body_and_redacts_control_headers(client):
    from orchestrator_api.main import app

    from mas_core.integrations.registry import ProviderRegistry

    connection_id = uuid4()
    secret = "raw-secret"
    body = json.dumps({"event": "ignored"}, separators=(",", ":")).encode()
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    storage = MagicMock()
    storage.get_pm_connection = AsyncMock(return_value={
        "id": connection_id,
        "provider_kind": "fake",
        "display_name": "fake",
        "base_url": "https://fake.example",
        "credential_ref": "fake-token",
        "capability_profile": "pm",
        "status": "ACTIVE",
        "config": {"webhook_secret_test_only": secret},
    })
    storage.create_pm_inbox_event = AsyncMock(return_value=({"id": uuid4(), "payload_hash": hashlib.sha256(body).hexdigest()}, True))
    storage.mark_pm_inbox_event = AsyncMock()
    app.state.storage = storage
    app.state.pm_registry = ProviderRegistry()

    response = await client.post(
        f"/integrations/webhooks/{connection_id}",
        content=body,
        headers={"X-Fake-Signature": signature, "X-API-Key": "test-gateway-key"},
    )
    assert response.status_code == 202
    call = storage.create_pm_inbox_event.await_args.kwargs
    assert call["raw_body"] == body
    assert "x-api-key" not in call["headers"]
    assert call["payload_hash"] == hashlib.sha256(body).hexdigest()


@pytest.mark.anyio
async def test_youtrack_webhook_valid_token_reaches_normalization_without_api_key(monkeypatch):
    from orchestrator_api import main
    from mas_core.integrations.registry import ProviderRegistry

    connection_id = uuid4()
    body = json.dumps(
        {
            "issue": {
                "id": "3-19",
                "summary": "provider-authenticated",
                "project": {"id": "0-0"},
                "updatedBy": {"login": "human"},
            }
        },
        separators=(",", ":"),
    ).encode()
    storage = MagicMock()
    storage.get_pm_connection = AsyncMock(
        return_value={
            "id": connection_id,
            "provider_kind": "youtrack",
            "display_name": "youtrack",
            "base_url": "https://youtrack.example",
            "credential_ref": "youtrack-token",
            "status": "ACTIVE",
            "config": {"project_id": "0-0", "webhook_secret_ref": "youtrack-webhook-current"},
        }
    )
    storage.create_pm_inbox_event = AsyncMock(
        return_value=({"id": uuid4(), "payload_hash": hashlib.sha256(body).hexdigest()}, True)
    )
    storage.mark_pm_inbox_event = AsyncMock()
    monkeypatch.setattr(main, "_apply_normalized_command", AsyncMock(return_value={"status": "conflict", "reason": "test"}))
    app = main.app
    app.state.storage = storage
    app.state.pm_registry = ProviderRegistry(credential_resolver=AsyncMock(return_value="provider-token"))

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.post(
            f"/integrations/webhooks/{connection_id}",
            content=body,
            headers={
                "X-YouTrack-Token": "provider-token",
                "X-YouTrack-Delivery": "provider-valid-1",
                "X-YouTrack-Event": "issue.updated",
            },
        )

    assert response.status_code == 202
    assert response.json()["normalized"] is True
    storage.create_pm_inbox_event.assert_awaited_once()
    storage.mark_pm_inbox_event.assert_awaited_once()
    assert storage.create_pm_inbox_event.await_args.kwargs["raw_body"] == body


@pytest.mark.anyio
@pytest.mark.parametrize("token", [None, "wrong-token"])
async def test_youtrack_webhook_rejects_missing_or_invalid_token_without_api_key(monkeypatch, token):
    from orchestrator_api import main
    from mas_core.integrations.registry import ProviderRegistry

    connection_id = uuid4()
    storage = MagicMock()
    storage.get_pm_connection = AsyncMock(
        return_value={
            "id": connection_id,
            "provider_kind": "youtrack",
            "display_name": "youtrack",
            "base_url": "https://youtrack.example",
            "credential_ref": "youtrack-token",
            "status": "ACTIVE",
            "config": {"webhook_secret_ref": "youtrack-webhook-current"},
        }
    )
    storage.create_pm_inbox_event = AsyncMock()
    app = main.app
    app.state.storage = storage
    app.state.pm_registry = ProviderRegistry(credential_resolver=AsyncMock(return_value="provider-token"))
    headers = {"X-YouTrack-Delivery": "provider-invalid-1", "X-YouTrack-Event": "issue.updated"}
    if token is not None:
        headers["X-YouTrack-Token"] = token

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.post(
            f"/integrations/webhooks/{connection_id}",
            content=b'{"issue":{"id":"3-19"}}',
            headers=headers,
        )

    assert response.status_code == 401
    storage.create_pm_inbox_event.assert_not_called()


@pytest.mark.anyio
async def test_management_endpoint_still_rejects_unauthenticated_callers(client):
    from orchestrator_api import main

    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/integrations/connections")

    assert response.status_code == 401


@pytest.mark.anyio
async def test_received_inbox_event_is_replayed_after_crash_window(client):
    from orchestrator_api.main import app

    from mas_core.integrations.registry import ProviderRegistry

    connection_id = uuid4()
    secret = "raw-secret"
    body = json.dumps({"event": "ignored"}, separators=(",", ":")).encode()
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    inbox_id = uuid4()
    storage = MagicMock()
    storage.get_pm_connection = AsyncMock(return_value={
        "id": connection_id,
        "provider_kind": "fake",
        "display_name": "fake",
        "base_url": "https://fake.example",
        "credential_ref": "fake-token",
        "capability_profile": "pm",
        "status": "ACTIVE",
        "config": {"webhook_secret_test_only": secret},
    })
    storage.create_pm_inbox_event = AsyncMock(
        return_value=({"id": inbox_id, "payload_hash": hashlib.sha256(body).hexdigest(), "status": "RECEIVED"}, False)
    )
    storage.mark_pm_inbox_event = AsyncMock()
    app.state.storage = storage
    app.state.pm_registry = ProviderRegistry()

    response = await client.post(
        f"/integrations/webhooks/{connection_id}",
        content=body,
        headers={"X-Fake-Signature": signature, "X-API-Key": "test-gateway-key"},
    )
    assert response.status_code == 202
    assert response.json()["normalized"] is False
    storage.mark_pm_inbox_event.assert_awaited_once()


@pytest.mark.anyio
async def test_github_app_broker_scopes_exchange_without_persisting_private_key(client, monkeypatch):
    import base64

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from orchestrator_api import main

    from mas_core.integrations.contracts import ProviderConnection

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_key = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    monkeypatch.setattr(main, "_integration_secret", AsyncMock(return_value=private_key))

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, url, *, headers, json):
            assert url.endswith("/app/installations/77/access_tokens")
            assert json == {"repositories": ["app"], "permissions": {"contents": "write"}}
            token = headers["Authorization"].split(" ", 1)[1]
            header, claims, _signature = token.split(".")
            decoded_header = json_loads(base64.urlsafe_b64decode(header + "=="))
            decoded_claims = json_loads(base64.urlsafe_b64decode(claims + "=="))
            assert decoded_header["alg"] == "RS256"
            assert decoded_claims["iss"] == 123
            return httpx.Response(201, json={"token": "installation-token", "expires_at": "soon"}, request=httpx.Request("POST", url))

    def json_loads(value):
        return json.loads(value.decode())

    monkeypatch.setattr(main.httpx, "AsyncClient", lambda **_kwargs: FakeClient())
    connection = ProviderConnection(
        provider_kind="github",
        display_name="GitHub",
        base_url="https://api.github.com",
        credential_ref="installation-token",
        capability_profile="delivery",
        config={
            "repository": "acme/app",
            "github_app_id": "123",
            "github_installation_id": "77",
            "github_app_private_key_ref": "github-app-private-key",
        },
    )
    result = await main._integration_run_token_broker(connection, "acme/app", {"contents": "write"})
    assert result["token"] == "installation-token"


@pytest.mark.anyio
async def test_canonical_issue_create_rejects_foreign_sprint(client):
    from orchestrator_api.main import app

    project_id = uuid4()
    storage = MagicMock()
    storage.get_project = AsyncMock(return_value={"id": project_id})
    storage.get_sprint = AsyncMock(return_value={"id": uuid4(), "project_id": uuid4()})
    storage.create_issue = AsyncMock()
    app.state.storage = storage

    response = await client.post(
        f"/projects/{project_id}/issues",
        json={"title": "scoped", "sprint_id": str(storage.get_sprint.return_value["id"])},
        headers={"X-API-Key": "test-operator-key"},
    )

    assert response.status_code == 404
    storage.create_issue.assert_not_awaited()


def test_bootstrap_plan_request_rejects_inline_secret_material():
    from orchestrator_api.main import PMPlanRequest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        PMPlanRequest(desired={"api_token": "not-a-reference"})


@pytest.mark.anyio
async def test_typed_sprint_routes_keep_tasks_as_compatibility_only(client):
    from orchestrator_api.main import app

    project_id = uuid4()
    sprint_id = uuid4()
    storage = MagicMock()
    storage.get_project = AsyncMock(return_value={"id": project_id})
    storage.create_sprint = AsyncMock(
        return_value={"id": sprint_id, "project_id": project_id, "sprint_number": 1, "revision": 1}
    )
    storage.get_sprint = AsyncMock(
        return_value={"id": sprint_id, "project_id": project_id, "revision": 1, "status": "PLANNED"}
    )
    storage.update_sprint = AsyncMock()
    app.state.storage = storage

    created = await client.post(
        f"/projects/{project_id}/sprints",
        json={"sprint_number": 1, "goal": "typed"},
        headers={"X-API-Key": "test-operator-key"},
    )
    assert created.status_code == 201
    assert created.json()["sprint"]["id"] == str(sprint_id)
    updated = await client.patch(
        f"/projects/{project_id}/sprints/{sprint_id}",
        json={"status": "IN_PROGRESS", "expected_revision": 1},
        headers={"X-API-Key": "test-operator-key"},
    )
    assert updated.status_code == 200
    storage.create_sprint.assert_awaited_once()
    storage.update_sprint.assert_awaited_once()


@pytest.mark.anyio
async def test_typed_canonical_mutation_rejects_shared_service_key(client):
    project_id = uuid4()
    response = await client.post(
        f"/projects/{project_id}/sprints",
        json={"sprint_number": 1},
        headers={
            "X-API-Key": "test-mas-key",
            "X-AIAT-Actor-Role": "operator",
        },
    )
    assert response.status_code == 403


@pytest.mark.anyio
async def test_legacy_deterministic_task_rejects_shared_service_key(client):
    response = await client.post(
        "/tasks",
        json={
            "project_id": str(uuid4()),
            "payload": {"action": "CREATE_ISSUE", "title": "must be operator-only"},
        },
        headers={"X-API-Key": "test-mas-key", "X-AIAT-Actor-Role": "operator"},
    )
    assert response.status_code == 403


@pytest.mark.anyio
async def test_integration_role_header_cannot_elevate_shared_service_key(client):
    response = await client.get(
        "/integrations/connections",
        headers={
            "X-API-Key": "test-mas-key",
            "X-AIAT-Actor-Role": "operator",
        },
    )
    assert response.status_code == 403


def test_connection_response_redacts_secret_shaped_configuration():
    from orchestrator_api.main import _serialize_pm_connection

    connection = _serialize_pm_connection(
        {
            "id": uuid4(),
            "provider_kind": "fake",
            "credential_ref": "managed-token",
            "config": {
                "webhook_token_test_only": "raw-token",
                "nested": {"api_key": "raw-key", "webhook_secret_ref": "hook-ref"},
            },
        }
    )
    assert connection is not None
    assert "webhook_token_test_only" not in connection["config"]
    assert "api_key" not in connection["config"]["nested"]
    assert connection["config"]["nested"]["webhook_secret_ref"] == "hook-ref"


@pytest.mark.anyio
async def test_non_fake_webhook_cannot_use_test_only_secret(client):
    from orchestrator_api.main import app

    from mas_core.integrations.registry import ProviderRegistry

    connection_id = uuid4()
    storage = MagicMock()
    storage.get_pm_connection = AsyncMock(
        return_value={
            "id": connection_id,
            "provider_kind": "github",
            "display_name": "github",
            "base_url": "https://api.github.com",
            "credential_ref": "managed-token",
            "status": "ACTIVE",
            "config": {"repository": "acme/app", "webhook_secret_test_only": "raw"},
        }
    )
    app.state.storage = storage
    app.state.pm_registry = ProviderRegistry()
    response = await client.post(
        f"/integrations/webhooks/{connection_id}",
        content=b"{}",
        headers={
            "X-API-Key": "test-gateway-key",
            "X-Hub-Signature-256": "sha256=invalid",
        },
    )
    assert response.status_code == 503
    storage.create_pm_inbox_event.assert_not_called()


@pytest.mark.anyio
async def test_run_credential_requires_connection_repository_scope(client):
    from orchestrator_api.main import app

    connection_id = uuid4()
    storage = MagicMock()
    storage.get_pm_connection = AsyncMock(
        return_value={
            "id": connection_id,
            "provider_kind": "github",
            "display_name": "github",
            "base_url": "https://api.github.com",
            "credential_ref": "managed-token",
            "status": "ACTIVE",
            "config": {},
            "capability_profile": "delivery",
        }
    )
    app.state.storage = storage
    response = await client.post(
        f"/integrations/connections/{connection_id}/source-control/run-credentials",
        json={"payload": {"repository": "acme/app", "permissions": {"contents": "write"}}},
        headers={"X-API-Key": "test-operator-key"},
    )
    assert response.status_code == 409


@pytest.mark.anyio
async def test_projected_comment_marker_is_acknowledged_without_reprojection(client):
    from orchestrator_api.main import _apply_normalized_command

    from mas_core.integrations.contracts import NormalizedCommand, ObjectType

    connection_id = uuid4()
    issue_id = uuid4()
    project_id = uuid4()
    comment_id = uuid4()
    storage = MagicMock()
    storage.get_pm_mapping = AsyncMock(
        return_value={"aiat_object_id": issue_id, "external_id": "42", "provider_version": "1"}
    )
    storage.get_issue = AsyncMock(
        return_value={"id": issue_id, "project_id": project_id, "title": "issue", "revision": 2}
    )
    storage.list_pm_bindings = AsyncMock(
        return_value=[
            {"id": uuid4(), "project_id": project_id, "connection_id": connection_id, "direction": "inbound", "status": "ACTIVE"}
        ]
    )
    storage.get_pm_connection = AsyncMock(
        return_value={"id": connection_id, "provider_kind": "github", "status": "ACTIVE", "config": {"repository": "acme/app"}}
    )
    storage.list_work_item_comments = AsyncMock(return_value=[{"id": comment_id}])
    storage.upsert_pm_mapping = AsyncMock()
    storage.create_work_item_comment = AsyncMock()
    storage.create_work_item_comment_with_pm_projections = AsyncMock()
    storage.mark_pm_inbox_event = AsyncMock()
    command = NormalizedCommand(
        connection_id=connection_id,
        object_type=ObjectType.WORK_ITEM,
        external_id="42",
        operation="comment",
        external_repository="acme/app",
        fields={"comment": f"<!-- aiat:comment={comment_id} -->\nAIAT actor: operator\n\noriginal"},
        idempotency_key="comment-echo",
    )
    result = await _apply_normalized_command(storage, command, {"id": uuid4()})
    assert result["status"] == "echo"
    storage.create_work_item_comment.assert_not_awaited()
    storage.create_work_item_comment_with_pm_projections.assert_not_awaited()


@pytest.mark.anyio
async def test_allowlisted_webhook_without_actor_is_rejected(client):
    from orchestrator_api.main import _apply_normalized_command

    from mas_core.integrations.contracts import NormalizedCommand, ObjectType

    connection_id = uuid4()
    issue_id = uuid4()
    project_id = uuid4()
    storage = MagicMock()
    storage.get_pm_mapping = AsyncMock(return_value={"aiat_object_id": issue_id, "external_id": "42"})
    storage.get_issue = AsyncMock(return_value={"id": issue_id, "project_id": project_id, "revision": 1})
    storage.list_pm_bindings = AsyncMock(return_value=[{"id": uuid4(), "project_id": project_id, "connection_id": connection_id, "direction": "inbound", "status": "ACTIVE"}])
    storage.get_pm_connection = AsyncMock(return_value={"id": connection_id, "provider_kind": "fake", "status": "ACTIVE", "config": {"allowed_external_actors": ["approved"]}})
    storage.create_pm_conflict = AsyncMock()
    storage.mark_pm_inbox_event = AsyncMock()
    command = NormalizedCommand(
        connection_id=connection_id,
        object_type=ObjectType.WORK_ITEM,
        external_id="42",
        operation="update",
        fields={"title": "external"},
        idempotency_key="actor-missing",
    )
    result = await _apply_normalized_command(storage, command, {"id": uuid4()})
    assert result == {"status": "conflict", "reason": "unauthorized_external_actor"}
    storage.create_pm_conflict.assert_awaited_once()


def _active_command_fixture(*, fields: dict[str, object], actor_id: str = "human-admin", expected_revision: int | None = 1):
    from mas_core.integrations.contracts import NormalizedCommand, ObjectType

    connection_id = uuid4()
    issue_id = uuid4()
    project_id = uuid4()
    binding_id = uuid4()
    issue = {
        "id": issue_id,
        "project_id": project_id,
        "title": "canonical",
        "description": "body",
        "status": "backlog",
        "priority": "medium",
        "revision": 1,
    }
    storage = MagicMock()
    storage.get_pm_mapping = AsyncMock(return_value={
        "aiat_object_id": issue_id,
        "external_id": "42",
        "imported_revision": 1,
        "exported_revision": 1,
        "provider_version": "1",
    })
    storage.get_issue = AsyncMock(return_value=issue)
    storage.list_pm_bindings = AsyncMock(return_value=[
        {"id": binding_id, "project_id": project_id, "connection_id": connection_id, "direction": "inbound", "status": "ACTIVE"}
    ])
    storage.get_pm_connection = AsyncMock(return_value={
        "id": connection_id,
        "provider_kind": "fake",
        "base_url": "https://fake.example",
        "status": "ACTIVE",
        "config": {},
    })
    storage.get_pm_external_actor_mapping = AsyncMock(return_value={"id": uuid4(), "status": "TRUSTED", "aiat_identity_id": "operator", "authorized_scopes": ["issue.priority"]})
    storage.update_issue = AsyncMock()
    storage.upsert_pm_mapping = AsyncMock()
    storage.mark_pm_inbox_event = AsyncMock()
    storage.create_pm_conflict = AsyncMock()
    command = NormalizedCommand(
        connection_id=connection_id,
        binding_id=binding_id,
        object_type=ObjectType.WORK_ITEM,
        external_id="42",
        operation="update",
        fields=fields,
        expected_canonical_revision=expected_revision,
        actor={"actor_id": actor_id, "immutable_actor_id": True, "role": "human"} if actor_id is not None else None,
        idempotency_key=f"active:{uuid4()}",
    )
    return storage, command, issue


@pytest.mark.anyio
async def test_active_allowlist_applies_priority_with_human_actor_and_cas(client):
    from orchestrator_api.main import _apply_normalized_command

    storage, command, issue = _active_command_fixture(fields={"priority": "high"})
    storage.get_issue = AsyncMock(side_effect=[issue, {**issue, "priority": "high", "revision": 2}])
    result = await _apply_normalized_command(storage, command, {"id": uuid4(), "payload_hash": "hash"})
    assert result["status"] == "applied"
    storage.update_issue.assert_awaited_once_with(issue["id"], expected_revision=1, priority="high")


@pytest.mark.anyio
async def test_active_title_change_is_approval_required_and_does_not_mutate(client):
    from orchestrator_api.main import _apply_normalized_command

    storage, command, issue = _active_command_fixture(fields={"title": "provider title"})
    result = await _apply_normalized_command(storage, command, {"id": uuid4()})
    assert result == {"status": "conflict", "reason": "approval_required", "field": "title"}
    storage.update_issue.assert_not_awaited()
    assert storage.create_pm_conflict.await_args.kwargs["reason"] == "approval_required"


@pytest.mark.anyio
async def test_active_reserved_identity_field_is_rejected(client):
    from orchestrator_api.main import _apply_normalized_command

    storage, command, _ = _active_command_fixture(fields={"AIAT Managed": False})
    result = await _apply_normalized_command(storage, command, {"id": uuid4()})
    assert result["reason"] == "reserved_field_mutation"
    storage.update_issue.assert_not_awaited()


@pytest.mark.anyio
async def test_active_synthetic_actor_and_stale_revision_are_rejected(client):
    from orchestrator_api.main import _apply_normalized_command

    storage, command, _ = _active_command_fixture(fields={"priority": "high"}, actor_id="AIAT_Agents")
    result = await _apply_normalized_command(storage, command, {"id": uuid4()})
    assert result["reason"] == "unauthorized_external_actor"
    storage, command, _ = _active_command_fixture(fields={"priority": "high"}, expected_revision=2)
    result = await _apply_normalized_command(storage, command, {"id": uuid4()})
    assert result["reason"] == "stale_revision"


@pytest.mark.anyio
async def test_active_comments_are_evidence_only_and_structured_comments_need_approval(client):
    from orchestrator_api.main import _apply_normalized_command

    storage, command, issue = _active_command_fixture(fields={"comment": "ordinary human note"})
    command = command.model_copy(update={"operation": "comment"})
    result = await _apply_normalized_command(storage, command, {"id": uuid4(), "event_type": "commentAdded", "payload_hash": "hash"})
    assert result["status"] == "evidence_only"
    storage.update_issue.assert_not_awaited()
    storage, command, _ = _active_command_fixture(fields={"comment": 'AIAT-COMMAND: {"action":"close"}'})
    command = command.model_copy(update={"operation": "comment"})
    result = await _apply_normalized_command(storage, command, {"id": uuid4(), "event_type": "commentAdded"})
    assert result["reason"] == "approval_required"


@pytest.mark.anyio
async def test_reconcile_uses_binding_cursor_and_processes_full_provider_page(client):
    from orchestrator_api.main import app

    from mas_core.integrations.contracts import ExternalObject, ObjectType

    connection_id = uuid4()
    binding_id = uuid4()
    issue_one = uuid4()
    issue_two = uuid4()
    row = {
        "id": connection_id,
        "provider_kind": "fake",
        "display_name": "fake",
        "base_url": "https://fake.example",
        "credential_ref": "fake-ref",
        "capability_profile": "pm",
        "status": "ACTIVE",
        "config": {},
    }
    binding = {"id": binding_id, "connection_id": connection_id, "status": "ACTIVE", "sync_cursor": "cursor-1"}
    provider = MagicMock()
    provider.list_changes = AsyncMock(
        return_value=(
            [
                ExternalObject(object_type=ObjectType.WORK_ITEM, external_id="one", content_hash="h1"),
                ExternalObject(object_type=ObjectType.WORK_ITEM, external_id="two", content_hash="h2"),
            ],
            "cursor-2",
        )
    )
    storage = MagicMock()
    storage.get_pm_connection = AsyncMock(return_value=row)
    storage.list_pm_bindings = AsyncMock(return_value=[binding])
    storage.list_pm_conflicts = AsyncMock(return_value=[])
    storage.get_pm_mapping = AsyncMock(side_effect=[{"aiat_object_id": issue_one, "content_hash": "h1"}, {"aiat_object_id": issue_two, "content_hash": "h2"}])
    storage.update_pm_binding = AsyncMock()
    app.state.storage = storage
    registry = MagicMock()
    registry.get.return_value = provider
    app.state.pm_registry = registry
    response = await client.post(
        f"/integrations/connections/{connection_id}/reconcile",
        json={"limit": 1},
        headers={"X-API-Key": "test-operator-key"},
    )
    assert response.status_code == 200
    assert response.json()["seen"] == 2
    provider.list_changes.assert_awaited_once()
    assert provider.list_changes.await_args.kwargs["cursor"] == "cursor-1"
    update_call = storage.update_pm_binding.await_args
    assert update_call.args == (binding_id,)
    assert update_call.kwargs["sync_cursor"] == "cursor-2"
    assert update_call.kwargs["last_reconciled_at"] is not None


@pytest.mark.anyio
async def test_bootstrap_apply_rejects_fabricated_plan(client):
    from orchestrator_api.main import app

    from mas_core.integrations.contracts import BootstrapPlan
    from mas_core.integrations.registry import ProviderRegistry

    connection_id = uuid4()
    plan = BootstrapPlan(connection_id=connection_id, provider_kind="fake")
    storage = MagicMock()
    storage.get_pm_connection = AsyncMock(
        return_value={
            "id": connection_id,
            "provider_kind": "fake",
            "display_name": "fake",
            "base_url": "https://fake.example",
            "credential_ref": "fake-ref",
            "status": "DISABLED",
            "config": {},
        }
    )
    app.state.storage = storage
    app.state.pm_registry = ProviderRegistry()
    response = await client.post(
        f"/integrations/connections/{connection_id}/apply",
        json={"plan": plan.model_dump(mode="json"), "plan_digest": plan.digest(), "confirm": True},
        headers={"X-API-Key": "test-operator-key"},
    )
    assert response.status_code == 409


@pytest.mark.anyio
async def test_youtrack_doctor_requires_project_creator_and_rejects_global_admin(client):
    from orchestrator_api.main import app

    connection_id = uuid4()
    row = {
        "id": connection_id,
        "provider_kind": "youtrack",
        "display_name": "YouTrack",
        "base_url": "https://youtrack.example",
        "credential_ref": "yt-ref",
        "capability_profile": "pm",
        "status": "ACTIVE",
        "config": {"project_id": "0-0", "webhook_secret_ref": "hook-ref"},
    }
    provider = MagicMock()
    provider.health = AsyncMock(return_value={"ok": True})
    provider.capabilities = AsyncMock(return_value={"projects": True})
    provider.verify_configuration = AsyncMock(return_value={"ok": True})
    provider.verify_least_privilege = AsyncMock(
        return_value={
            "ok": False,
            "missing": [],
            "forbidden": ["system_admin", "low_level_admin_write", "delete_project"],
            "observed": {"global": ["observer", "project_creator"]},
        }
    )
    provider.discover = AsyncMock(return_value={"projects": []})
    storage = MagicMock()
    storage.get_pm_connection = AsyncMock(return_value=row)
    storage.list_pm_bindings = AsyncMock(return_value=[{"id": uuid4()}])
    app.state.storage = storage
    registry = MagicMock()
    registry.get.return_value = provider
    app.state.pm_registry = registry

    response = await client.get(
        f"/integrations/connections/{connection_id}/doctor",
        headers={"X-API-Key": "test-operator-key"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ready"] is False
    least_privilege = next(check for check in body["checks"] if check["name"] == "least_privilege")
    assert least_privilege["ok"] is False
    assert "forbidden permission: system_admin" in least_privilege["error"]


@pytest.mark.anyio
async def test_youtrack_doctor_passes_certified_project_creator_boundary(client):
    from orchestrator_api.main import app

    connection_id = uuid4()
    row = {
        "id": connection_id,
        "provider_kind": "youtrack",
        "display_name": "YouTrack",
        "base_url": "https://youtrack.example",
        "credential_ref": "yt-ref",
        "capability_profile": "pm",
        "status": "ACTIVE",
        "config": {"project_id": "0-0", "webhook_secret_ref": "hook-ref"},
    }
    provider = MagicMock()
    provider.health = AsyncMock(return_value={"ok": True})
    provider.capabilities = AsyncMock(return_value={"projects": True})
    provider.verify_configuration = AsyncMock(return_value={"ok": True})
    provider.verify_least_privilege = AsyncMock(
        return_value={
            "ok": True,
            "missing": [],
            "forbidden": [],
            "expected": {"global_permissions": ["Create Project"]},
            "observed": {"global": ["observer", "project_creator", "create_project"]},
        }
    )
    provider.discover = AsyncMock(return_value={"projects": []})
    storage = MagicMock()
    storage.get_pm_connection = AsyncMock(return_value=row)
    storage.list_pm_bindings = AsyncMock(return_value=[{"id": uuid4()}])
    app.state.storage = storage
    registry = MagicMock()
    registry.get.return_value = provider
    app.state.pm_registry = registry

    response = await client.get(
        f"/integrations/connections/{connection_id}/doctor",
        headers={"X-API-Key": "test-operator-key"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ready"] is True
    least_privilege = next(check for check in body["checks"] if check["name"] == "least_privilege")
    assert least_privilege["ok"] is True


@pytest.mark.anyio
async def test_youtrack_cannot_be_activated_without_least_privilege_certification(client):
    from orchestrator_api.main import app

    connection_id = uuid4()
    row = {
        "id": connection_id,
        "provider_kind": "youtrack",
        "display_name": "YouTrack",
        "base_url": "https://youtrack.example",
        "credential_ref": "yt-ref",
        "capability_profile": "pm",
        "status": "SHADOW",
        "config": {},
    }
    provider = MagicMock()
    provider.verify_least_privilege = AsyncMock(
        return_value={"ok": False, "missing": ["Create Project"], "forbidden": []}
    )
    storage = MagicMock()
    storage.get_pm_connection = AsyncMock(return_value=row)
    storage.update_pm_connection = AsyncMock(return_value={**row, "status": "ACTIVE"})
    app.state.storage = storage
    registry = MagicMock()
    registry.get.return_value = provider
    app.state.pm_registry = registry

    response = await client.patch(
        f"/integrations/connections/{connection_id}/status",
        json={"status": "ACTIVE"},
        headers={"X-API-Key": "test-operator-key"},
    )

    assert response.status_code == 409
    assert "Create Project" in response.json()["detail"]
    storage.update_pm_connection.assert_not_awaited()


@pytest.mark.anyio
async def test_default_binding_requires_dedicated_provider_project_selector(client):
    from orchestrator_api.main import app

    project_id = uuid4()
    connection_id = uuid4()
    storage = MagicMock()
    storage.get_project = AsyncMock(return_value={"id": project_id})
    storage.get_pm_connection = AsyncMock(
        return_value={"id": connection_id, "provider_kind": "fake", "status": "DISABLED", "config": {}}
    )
    app.state.storage = storage
    response = await client.post(
        f"/projects/{project_id}/pm-bindings",
        json={"connection_id": str(connection_id)},
        headers={"X-API-Key": "test-operator-key"},
    )
    assert response.status_code == 422
    assert "provider project" in response.json()["detail"]
    storage.create_pm_binding.assert_not_called()


def test_active_binding_requires_webhook_projection_and_reconciliation_evidence():
    from mas_core.memory.storage import AgentStorage

    with pytest.raises(ValueError, match="webhook"):
        AgentStorage._assert_pm_binding_activation_ready(
            {
                "external_project_id": "0-1",
                "activation_blockers": [],
                "webhook_events": [],
            },
            {"status": "ACTIVE"},
        )


def test_active_umbrella_binding_accepts_repository_selector():
    from mas_core.memory.storage import AgentStorage

    AgentStorage._assert_pm_binding_activation_ready(
        {
            "mapping_profile": "umbrella_issues",
            "external_repository": "acme/app",
            "activation_blockers": [],
            "webhook_events": ["issue", "comment"],
            "webhook_verified_at": "2026-07-30T00:00:00Z",
            "projection_verified_at": "2026-07-30T00:00:00Z",
            "reconciliation_verified_at": "2026-07-30T00:00:00Z",
        },
        {"status": "ACTIVE"},
    )


@pytest.mark.anyio
async def test_lifecycle_plan_generation_persists_immutable_payload(client, monkeypatch):
    from datetime import UTC, datetime, timedelta
    from orchestrator_api import main

    connection_id = uuid4()
    binding_id = uuid4()
    project_id = uuid4()
    connection = {
        "id": connection_id,
        "provider_kind": "fake",
        "display_name": "fake",
        "base_url": "https://fake.example",
        "credential_ref": "fake-ref",
        "capability_profile": "pm",
        "status": "SHADOW",
        "revision": 3,
        "config": {},
    }
    binding = {
        "id": binding_id,
        "project_id": project_id,
        "connection_id": connection_id,
        "status": "SHADOW",
        "revision": 4,
        "direction": "both",
        "external_project_id": "0-1",
        "mapping_profile": "dedicated_project",
    }
    now = datetime.now(tz=UTC)
    storage = MagicMock()
    storage.get_pm_connection = AsyncMock(return_value=connection)
    storage.list_pm_bindings = AsyncMock(return_value=[binding])
    storage.list_pm_reconciliation_runs = AsyncMock(return_value=[{
        "id": uuid4(),
        "status": "COMPLETED",
        "counts": {"seen": 1, "mapped": 1, "drift": 0, "conflicts": 0, "scope_conflicts": 0, "version_mismatches": 0, "hash_mismatches": 0},
    }])
    storage.list_pm_conflicts = AsyncMock(return_value=[])
    storage.list_pm_outbox = AsyncMock(return_value=[])

    async def fake_doctor(_connection_id, _request):
        return {"connection_id": str(connection_id), "ready": True, "blockers": [], "checks": []}

    monkeypatch.setattr(main, "doctor_integration_connection", fake_doctor)

    async def persist(plan, *, digest):
        assert plan.digest() == digest
        return {
            "id": plan.plan_id,
            "plan_kind": plan.plan_kind,
            "schema_version": plan.schema_version,
            "target_type": plan.target_type,
            "target_id": plan.target_id,
            "connection_id": plan.connection_id,
            "binding_id": plan.binding_id,
            "expected_connection_status": plan.expected_connection_status,
            "expected_binding_status": plan.expected_binding_status,
            "expected_connection_revision": plan.expected_connection_revision,
            "expected_binding_revision": plan.expected_binding_revision,
            "desired_connection_status": plan.desired_connection_status,
            "desired_binding_status": plan.desired_binding_status,
            "observed_versions": plan.observed_versions,
            "operations": plan.operations,
            "gate_results": plan.gate_results,
            "evidence_refs": plan.evidence_refs,
            "blockers": plan.blockers,
            "rollback_operations": plan.rollback_operations,
            "created_by": plan.created_by,
            "created_at": plan.created_at,
            "expires_at": plan.expires_at,
            "digest": digest,
            "status": "PLANNED",
        }

    storage.create_pm_lifecycle_plan = AsyncMock(side_effect=persist)
    main.app.state.storage = storage
    response = await client.post(
        "/api/v1/integrations/lifecycle-plans",
        json={
            "target_type": "pm_binding",
            "connection_id": str(connection_id),
            "binding_id": str(binding_id),
            "desired_binding_status": "READ_ONLY",
            "ttl_seconds": 3600,
        },
        headers={"X-API-Key": "test-operator-key"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "PLANNED"
    assert body["digest_valid"] is True
    assert body["plan"]["operations"] == [{"operation": "set_binding_status", "binding_id": str(binding_id), "from": "SHADOW", "to": "READ_ONLY"}]
    storage.create_pm_lifecycle_plan.assert_awaited_once()


@pytest.mark.anyio
async def test_lifecycle_plan_allows_emergency_connection_disable_when_readiness_fails(client, monkeypatch):
    from orchestrator_api import main

    connection_id = uuid4()
    connection = {
        "id": connection_id,
        "provider_kind": "fake",
        "display_name": "fake",
        "base_url": "https://fake.example",
        "credential_ref": "fake-ref",
        "capability_profile": "pm",
        "status": "ACTIVE",
        "revision": 3,
        "config": {},
    }
    storage = MagicMock()
    storage.get_pm_connection = AsyncMock(return_value=connection)
    storage.list_pm_reconciliation_runs = AsyncMock(return_value=[])
    storage.list_pm_conflicts = AsyncMock(return_value=[])
    storage.list_pm_outbox = AsyncMock(return_value=[])

    async def failed_doctor(_connection_id, _request):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(main, "doctor_integration_connection", failed_doctor)

    async def persist(plan, *, digest):
        row = plan.model_dump(mode="python")
        row["id"] = row.pop("plan_id")
        row["digest"] = digest
        row["status"] = "PLANNED"
        return row

    storage.create_pm_lifecycle_plan = AsyncMock(side_effect=persist)
    main.app.state.storage = storage
    response = await client.post(
        "/api/v1/integrations/lifecycle-plans",
        json={
            "target_type": "pm_connection",
            "connection_id": str(connection_id),
            "desired_connection_status": "DISABLED",
            "ttl_seconds": 3600,
        },
        headers={"X-API-Key": "test-operator-key"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["plan"]["blockers"] == []
    assert body["plan"]["gate_results"]["readiness_gates_bypassed"] is True
    assert body["plan"]["gate_results"]["readiness_gate_blockers"]


@pytest.mark.anyio
async def test_emergency_connection_disable_apply_ignores_fresh_readiness_failure(client, monkeypatch):
    from datetime import UTC, datetime, timedelta

    from orchestrator_api import main

    from mas_core.integrations.contracts import PMLifecycleTransitionPlan

    connection_id = uuid4()
    now = datetime.now(tz=UTC)
    connection = {
        "id": connection_id,
        "provider_kind": "fake",
        "display_name": "fake",
        "base_url": "https://fake.example",
        "credential_ref": "fake-ref",
        "capability_profile": "pm",
        "status": "ACTIVE",
        "revision": 3,
        "config": {},
    }
    plan = PMLifecycleTransitionPlan(
        plan_kind="pm_connection_transition",
        target_type="pm_connection",
        target_id=connection_id,
        connection_id=connection_id,
        expected_connection_status="ACTIVE",
        expected_connection_revision=3,
        desired_connection_status="DISABLED",
        operations=[{"operation": "set_connection_status", "to": "DISABLED"}],
        gate_results={
            "readiness_gates_bypassed": True,
            "readiness_gate_blockers": ["provider unavailable"],
        },
        rollback_operations=[{"operation": "set_connection_status", "to": "ACTIVE"}],
        created_by="operator",
        created_at=now,
        expires_at=now + timedelta(hours=1),
    )
    row = plan.model_dump(mode="python")
    row["id"] = row.pop("plan_id")
    row.update({"digest": plan.digest(), "status": "APPROVED", "approval_actor": "operator", "approved_at": now})
    applied_row = {**row, "status": "APPLIED", "application_result": {"ok": True}}
    storage = MagicMock()
    storage.get_pm_lifecycle_plan = AsyncMock(return_value=row)
    storage.get_pm_connection = AsyncMock(return_value=connection)
    storage.list_pm_reconciliation_runs = AsyncMock(return_value=[])
    storage.list_pm_conflicts = AsyncMock(return_value=[])
    storage.list_pm_outbox = AsyncMock(return_value=[])
    storage.apply_pm_lifecycle_plan = AsyncMock(
        return_value={"status": "APPLIED", "plan": applied_row, "result": {"ok": True}, "idempotent": False}
    )

    async def failed_doctor(_connection_id, _request):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(main, "doctor_integration_connection", failed_doctor)
    main.app.state.storage = storage
    response = await client.post(
        f"/api/v1/integrations/lifecycle-plans/{plan.plan_id}/apply",
        json={"plan_digest": plan.digest(), "confirm": True},
        headers={"X-API-Key": "test-operator-key"},
    )

    assert response.status_code == 200
    assert response.json()["application"] == {"ok": True}
    storage.apply_pm_lifecycle_plan.assert_awaited_once()


@pytest.mark.anyio
async def test_lifecycle_plan_approval_cannot_be_elevated_by_actor_header(client):
    from orchestrator_api import main

    plan_id = uuid4()
    response = await client.post(
        f"/api/v1/integrations/lifecycle-plans/{plan_id}/approve",
        json={"plan_digest": "0" * 64},
        headers={"X-API-Key": "test-mas-key", "X-AIAT-Actor-Role": "operator"},
    )
    assert response.status_code == 403


@pytest.mark.anyio
async def test_lifecycle_plan_apply_requires_explicit_confirmation(client):
    from orchestrator_api import main

    plan_id = uuid4()
    storage = MagicMock()
    storage.get_pm_lifecycle_plan = AsyncMock(return_value=None)
    main.app.state.storage = storage
    response = await client.post(
        f"/api/v1/integrations/lifecycle-plans/{plan_id}/apply",
        json={"plan_digest": "0" * 64, "confirm": False},
        headers={"X-API-Key": "test-operator-key"},
    )
    assert response.status_code == 400


@pytest.mark.anyio
async def test_legacy_cutover_and_rollback_cannot_bypass_lifecycle_plan(client):
    from orchestrator_api import main

    main.app.state.storage = MagicMock()
    project_id = uuid4()
    binding_id = uuid4()
    for path in ("/integrations/cutovers", "/integrations/rollbacks"):
        response = await client.post(
            path,
            json={"project_id": str(project_id), "binding_id": str(binding_id), "confirm": True},
            headers={"X-API-Key": "test-operator-key"},
        )
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "lifecycle_plan_required"
    main.app.state.storage.cutover_pm_binding.assert_not_called()
