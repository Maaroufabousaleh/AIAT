from __future__ import annotations

import base64
import hashlib
import json
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
