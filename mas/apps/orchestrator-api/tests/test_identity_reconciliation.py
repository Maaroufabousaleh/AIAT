from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from orchestrator_api.identity_client import IdentityClientConfig, SignedIdentityClient
from orchestrator_api.tool_service_client import SignedToolServiceClient, ToolServiceClientConfig
from pydantic import ValidationError


class _LifecycleStorage:
    def __init__(self) -> None:
        self.cursor = 0
        self.rows: dict[object, dict] = {}

    async def get_identity_reconciliation_cursor(self, _client_id: str) -> int:
        return self.cursor

    async def set_identity_reconciliation_cursor(self, _client_id: str, cursor: int) -> None:
        self.cursor = max(self.cursor, cursor)

    async def upsert_worker_identity_lifecycle(self, *, worker_id, state, **values):
        row = self.rows.setdefault(worker_id, {"worker_id": worker_id})
        row.update({"state": state, **{key: value for key, value in values.items() if value is not None}})
        return row


@pytest.mark.anyio
async def test_signed_outbox_reconciliation_is_cursor_based_and_replay_safe() -> None:
    worker_id = uuid4()
    event = {
        "id": str(uuid4()), "sequence": 7, "event_type": "mailbox.identity_active",
        "payload_json": {"worker_id": str(worker_id), "address": "w-test@agents.aiat.ca"},
    }

    acknowledgements: list[int] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-AIAT-Signature-Version"] == "aiat.identity.v1"
        body = json.loads(request.content)
        if request.url.path == "/v1/sync/ack":
            acknowledgements.append(body["cursor"])
            return httpx.Response(200, json={"client_id": "operator-laptop", "acknowledged_cursor": body["cursor"]})
        assert request.url.path == "/v1/sync/events"
        assert body["cursor"] in {0, 7}
        events = [event] if body["cursor"] == 0 else []
        return httpx.Response(200, json={"events": events, "next_cursor": 7})

    private = Ed25519PrivateKey.generate().private_bytes_raw()
    config = IdentityClientConfig(
        url="https://identity.example", client_id="operator-laptop",
        private_key_b64=base64.b64encode(private).decode(),
    )
    storage = _LifecycleStorage()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        identity_client = SignedIdentityClient(config, client=http_client)
        await identity_client.reconcile_worker_lifecycle(storage)
        await identity_client.reconcile_worker_lifecycle(storage)

    assert storage.cursor == 7
    assert acknowledgements == [7]
    assert storage.rows[worker_id]["state"] == "IDENTITY_ACTIVE"
    assert storage.rows[worker_id]["identity_address"] == "w-test@agents.aiat.ca"


@pytest.mark.anyio
async def test_mail_delivery_projection_is_scalar_and_windowed() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/dashboard/mail-relay"
        assert json.loads(request.content) == {"limit": 100}
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "outbound_request_id": "mail-current",
                        "provider_message_id": "secret-provider-id",
                        "outcome": "submitted",
                        "attempted_at": "2026-08-10T00:00:02+00:00",
                        "sanitized_reason": "never returned",
                    },
                    {
                        "outbound_request_id": "mail-old",
                        "outcome": "failed",
                        "attempted_at": "2026-08-01T00:00:00+00:00",
                    },
                ]
            },
        )

    private = Ed25519PrivateKey.generate().private_bytes_raw()
    config = IdentityClientConfig(
        url="https://identity.example",
        client_id="operator-laptop",
        private_key_b64=base64.b64encode(private).decode(),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        identity_client = SignedIdentityClient(config, client=http_client)
        rows = await identity_client.list_mail_delivery_observations(
            since=datetime(2026, 8, 10, tzinfo=UTC),
            limit=100,
        )

    assert rows == [
        {
            "id": "mail-current",
            "status": "success",
            "occurred_at": "2026-08-10T00:00:02+00:00",
            "source": "identity_outbound_delivery_attempts",
        }
    ]
    assert "secret-provider-id" not in str(rows)
    assert "never returned" not in str(rows)


@pytest.mark.anyio
async def test_mail_delivery_projection_can_filter_and_keep_only_safe_trace_ids() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/dashboard/mail-relay"
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "outbound_request_id": "mail-match",
                        "provider_message_id": "must-drop",
                        "provider_correlation_id": "must-drop-too",
                        "outcome": "submitted",
                        "attempted_at": "2026-08-10T00:00:02+00:00",
                        "trace_id": "trace-mail-001",
                        "span_id": "span-mail-001",
                        "recipients": ["recipient@example.net"],
                    },
                    {
                        "outbound_request_id": "mail-other",
                        "outcome": "failed",
                        "attempted_at": "2026-08-10T00:00:03+00:00",
                        "trace_id": "trace-other",
                        "span_id": "span-other",
                    },
                ],
            },
        )

    private = Ed25519PrivateKey.generate().private_bytes_raw()
    config = IdentityClientConfig(
        url="https://identity.example",
        client_id="operator-laptop",
        private_key_b64=base64.b64encode(private).decode(),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        identity_client = SignedIdentityClient(config, client=http_client)
        rows = await identity_client.list_mail_delivery_observations(
            trace_id="trace-mail-001",
            limit=100,
        )

    assert rows == [
        {
            "id": "mail-match",
            "status": "success",
            "occurred_at": "2026-08-10T00:00:02+00:00",
            "source": "identity_outbound_delivery_attempts",
            "trace_id": "trace-mail-001",
            "span_id": "span-mail-001",
        }
    ]
    assert "must-drop" not in str(rows)
    assert "recipient@example.net" not in str(rows)
    assert await identity_client.list_mail_delivery_observations(trace_id="unsafe trace") == []


@pytest.mark.anyio
async def test_provider_webhook_projection_keeps_event_shape_but_drops_provider_payload() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/dashboard/mail-relay"
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "edge-observation-1",
                        "outbound_request_id": "mail-1",
                        "provider_message_id": "provider-secret",
                        "source": "provider_webhook",
                        "event_type": "bounced",
                        "outcome": "failure",
                        "occurred_at": "2026-08-17T12:00:00+00:00",
                        "trace_id": "trace-mail-edge-001",
                        "span_id": "span-mail-edge-001",
                        "metadata": {"provider_reason_code": "550", "body": "drop"},
                    }
                ]
            },
        )

    private = Ed25519PrivateKey.generate().private_bytes_raw()
    config = IdentityClientConfig(
        url="https://identity.example",
        client_id="operator-laptop",
        private_key_b64=base64.b64encode(private).decode(),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        identity_client = SignedIdentityClient(config, client=http_client)
        rows = await identity_client.list_mail_delivery_observations(trace_id="trace-mail-edge-001")

    assert rows == [
        {
            "id": "edge-observation-1",
            "status": "failed",
            "occurred_at": "2026-08-17T12:00:00+00:00",
            "source": "identity_mail_edge_provider_webhook",
            "event_type": "bounced",
            "trace_id": "trace-mail-edge-001",
            "span_id": "span-mail-edge-001",
        }
    ]
    assert "provider-secret" not in str(rows)
    assert "drop" not in str(rows)


@pytest.mark.anyio
async def test_dashboard_identity_actions_are_explicitly_allowlisted(monkeypatch: pytest.MonkeyPatch) -> None:
    from orchestrator_api import main

    calls: list[tuple[str, str, dict]] = []

    class StubIdentityClient:
        async def request(self, method: str, path: str, body: dict) -> dict:
            calls.append((method, path, body))
            return {"ok": True}

    monkeypatch.setattr(main, "_identity_client", lambda: StubIdentityClient())
    monkeypatch.setattr(main, "_tool_service_client", lambda: StubIdentityClient())
    approval_id = uuid4()
    result = await main.identity_dashboard_action(
        main.IdentityDashboardActionRequest(action="approval.approve", id=approval_id), None,
    )
    assert result == {"ok": True}
    assert calls == [("POST", f"/v1/approvals/{approval_id}/decision", {
        "actor": {"actor_id": "dashboard-operator", "purpose": "dashboard operator decision"},
        "approved": True, "reason": "dashboard operator decision",
    })]
    with pytest.raises(ValidationError):
        main.IdentityDashboardActionRequest(action="identity.arbitrary")

    calls.clear()
    worker_id = uuid4()
    await main._provision_identity_tool_grants(worker_id)
    assert len(calls) == len(main._IDENTITY_TOOL_GRANTS) + 1
    assert {path for _, path, _ in calls if path.endswith("/grants")} == {
        f"/tools/workers/{worker_id}/grants" for _ in main._IDENTITY_TOOL_GRANTS
    }
    assert {body["tool_name"] for _, path, body in calls if path.endswith("/grants")} == set(main._IDENTITY_TOOL_GRANTS)
    assert calls[-1] == (
        "POST", f"/tools/workers/{worker_id}/browser-identity", {}
    )


def test_production_cannot_disable_required_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    from orchestrator_api import main

    monkeypatch.setenv("MAS_ENVIRONMENT", "production")
    monkeypatch.setenv("AIAT_IDENTITY_REQUIRED", "false")
    assert main._identity_required() is True
    monkeypatch.setenv("MAS_ENVIRONMENT", "development")
    assert main._identity_required() is False


def test_production_identity_client_rejects_plaintext_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAS_ENVIRONMENT", "production")
    private = base64.b64encode(Ed25519PrivateKey.generate().private_bytes_raw()).decode()
    with pytest.raises(ValueError, match="HTTPS"):
        SignedIdentityClient(IdentityClientConfig(
            url="http://identity.aiat.ca",
            client_id="operator-laptop",
            private_key_b64=private,
        ))


def test_orchestrator_tool_client_signs_the_exact_request_body() -> None:
    private_key = Ed25519PrivateKey.generate()
    client = SignedToolServiceClient(ToolServiceClientConfig(
        url="https://tools.example", secret="test-secret", client_id="orchestrator-api",
        private_key_b64=base64.b64encode(private_key.private_bytes_raw()).decode(),
    ))
    body = b'{"caller_id":"worker-a"}'
    headers = client._headers("POST", "/tools/mail.list/run", body)
    canonical = "\n".join((
        "aiat.tool.v1", "POST", "/tools/mail.list/run", headers["X-AIAT-Timestamp"],
        headers["X-AIAT-Nonce"], hashlib.sha256(body).hexdigest(),
    )).encode()
    private_key.public_key().verify(base64.b64decode(headers["X-AIAT-Signature"]), canonical)
    assert headers["X-AIAT-Client-ID"] == "orchestrator-api"
