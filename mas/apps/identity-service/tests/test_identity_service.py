from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from identity_service.clients.auth import SignedClient, verify_request
from identity_service.config import IdentitySettings
from identity_service.main import create_app
from identity_service.models import IdentityState
from identity_service.store import InMemoryIdentityStore


class FakeStalwart:
    def __init__(self):
        self.submission_count = 0

    async def create_mailbox(self, address, *, quota_mb, idempotency_key):
        return {"provider_account_id": f"account-{address}", "correlation_id": "provider-create"}

    async def find_mailbox(self, _address):
        return None

    async def get_mailbox(self, provider_account_id):
        return {
            "provider_account_id": provider_account_id,
            "correlation_id": "provider-get",
            "result": {"list": [{"id": provider_account_id}]},
        }

    async def add_alias(self, *_args):
        return {"correlation_id": "alias"}

    async def read_message(self, _account_id, message_id):
        return {"correlation_id": "provider-read", "result": {"list": [{"id": message_id, "receivedAt": "2026-07-28T20:00:00Z", "bodyValues": {"x": {"value": "Code 481516"}}}]}}

    async def list_messages(self, _account_id, *, limit, query=None):
        return {"result": {"ids": ["message-a"], "limit": limit, "query": query}, "correlation_id": "provider-list"}

    async def wait_for_message(self, *_args, **_kwargs):
        return None

    async def submit_outbound_message(self, *_args, **_kwargs):
        self.submission_count += 1
        return {"correlation_id": "provider-submit", "provider_message_id": "queued-1", "result": {"created": {"message": {"id": "queued-1"}}}}

    async def cancel_queued_message(self, *_args):
        return {"correlation_id": "provider-cancel"}

    async def mark_processed(self, *_args):
        return {"correlation_id": "provider-mark"}

    async def delete_message(self, *_args):
        return {"correlation_id": "provider-delete"}

    async def get_outbound_queue_status(self, *_args):
        return {"correlation_id": "provider-status", "result": {"list": []}}

    async def health_check(self):
        return {"healthy": True}

    async def disable_mailbox(self, *_args):
        return {"correlation_id": "provider-disable"}

    async def archive_mailbox(self, *_args):
        return {"correlation_id": "provider-archive"}


def _private_key_b64() -> str:
    return base64.b64encode(Ed25519PrivateKey.generate().private_bytes_raw()).decode()


@pytest.fixture
async def identity_client():
    operator_key = _private_key_b64()
    worker_a_key = _private_key_b64()
    worker_b_key = _private_key_b64()
    clients = {
        "operator": SignedClient.from_base64("operator", operator_key),
        "worker-a": SignedClient.from_base64("worker-a", worker_a_key),
        "worker-b": SignedClient.from_base64("worker-b", worker_b_key),
    }
    settings = IdentitySettings(
        identity_client_public_keys_json=json.dumps({name: client.public_key_base64() for name, client in clients.items()}),
        identity_client_scopes_json=json.dumps({
            "operator": ["identity:delegate", "identity:admin"],
            "worker-a": ["identity:delegate", "identity:browser-broker"],
            "worker-b": ["identity:delegate"],
        }),
        outbound_relay_certified=True,
    )
    app = create_app(settings=settings, store=InMemoryIdentityStore())
    async with app.router.lifespan_context(app):
        fake = FakeStalwart()
        app.state.identity_service.stalwart = fake
        app.state.identity_service.mailboxes.provider = fake
        app.state.identity_service.outbound.provider = fake
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://identity") as client:
            yield client, clients


async def _post(client, signer, path: str, body: dict, *, headers: dict | None = None):
    raw = json.dumps(body, separators=(",", ":"), sort_keys=True).encode()
    signed = headers or signer.sign_headers("POST", path, raw)
    return await client.post(path, content=raw, headers={"Content-Type": "application/json", **signed})


@pytest.mark.anyio
async def test_provisioning_is_idempotent_and_requires_jmap_delivery_evidence(identity_client):
    client, signers = identity_client
    company_id, worker_id = uuid4(), uuid4()
    body = {"company_id": str(company_id), "worker_id": str(worker_id), "friendly_alias": "finance", "actor": {"actor_id": "orchestrator-api", "purpose": "approved hiring"}, "idempotency_key": f"mailbox:{company_id}:{worker_id}"}
    first = await _post(client, signers["operator"], "/v1/worker-identities/provision", body)
    second = await _post(client, signers["operator"], "/v1/worker-identities/provision", body)
    assert first.status_code == second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert first.json()["state"] == "IDENTITY_VERIFYING"
    verify = await _post(client, signers["operator"], f"/v1/worker-identities/{worker_id}/verify", {"actor": {"actor_id": "orchestrator-api", "purpose": "delivery verification"}, "provider_message_id": "external-message-1"})
    assert verify.status_code == 200
    assert verify.json()["state"] == "IDENTITY_ACTIVE"
    store = client._transport.app.state.identity_store  # type: ignore[attr-defined]
    job = store.provisioning_jobs[f"mailbox:{company_id}:{worker_id}"]
    assert job["state"] == "VERIFYING" and job["attempt_count"] == 1
    assert str(store.email_aliases["finance@agents.aiat.ca"]["identity_id"]) == first.json()["id"]
    assert any(key[1:] == ("external-message-1", "DELIVERY_VERIFIED") for key in store.mail_events)
    extracted = await _post(client, signers["worker-a"], "/v1/mail/extract-code", {
        "worker_id": str(worker_id), "actor": {"actor_id": str(worker_id), "purpose": "verification"},
        "message_id": "external-message-1",
    })
    assert extracted.status_code == 200 and extracted.json()["code"] == "481516"
    transaction = next(iter(store.verification_transactions.values()))
    assert transaction["code_hash"] and "481516" not in json.dumps(transaction, default=str)


@pytest.mark.anyio
async def test_provisioning_retry_reconciles_provider_commit_without_duplicate_mailbox(
    identity_client,
) -> None:
    client, signers = identity_client
    company_id, worker_id = uuid4(), uuid4()

    class CrashAfterProviderCommit(FakeStalwart):
        def __init__(self) -> None:
            super().__init__()
            self.create_count = 0
            self.alias_attempts = 0

        async def create_mailbox(self, address, *, quota_mb, idempotency_key):
            self.create_count += 1
            return await super().create_mailbox(
                address, quota_mb=quota_mb, idempotency_key=idempotency_key
            )

        async def add_alias(self, *_args):
            self.alias_attempts += 1
            if self.alias_attempts == 1:
                raise RuntimeError("simulated post-provider crash")
            return {"correlation_id": "alias-retry"}

    provider = CrashAfterProviderCommit()
    service = client._transport.app.state.identity_service  # type: ignore[attr-defined]
    service.stalwart = provider
    service.mailboxes.provider = provider
    body = {
        "company_id": str(company_id),
        "worker_id": str(worker_id),
        "friendly_alias": "retry-proof",
        "actor": {"actor_id": "orchestrator-api", "purpose": "approved hiring"},
        "idempotency_key": f"mailbox:{company_id}:{worker_id}",
    }
    with pytest.raises(RuntimeError, match="simulated post-provider crash"):
        await _post(
            client, signers["operator"], "/v1/worker-identities/provision", body
        )
    recovered = await _post(
        client, signers["operator"], "/v1/worker-identities/provision", body
    )
    assert recovered.status_code == 200
    assert recovered.json()["state"] == "IDENTITY_VERIFYING"
    assert provider.create_count == 1
    assert provider.alias_attempts == 2


@pytest.mark.anyio
async def test_stale_running_provisioning_job_resumes_after_process_crash(
    identity_client,
) -> None:
    client, signers = identity_client
    company_id, worker_id = uuid4(), uuid4()
    idempotency_key = f"mailbox:{company_id}:{worker_id}"
    store = client._transport.app.state.identity_store  # type: ignore[attr-defined]
    identity, _ = await store.provision_identity(
        company_id=company_id,
        worker_id=worker_id,
        address=f"w-{worker_id}@agents.aiat.ca",
        alias=None,
        domain="agents.aiat.ca",
        idempotency_key=idempotency_key,
        quota_mb=100,
    )
    await store.set_identity_state(
        worker_id,
        IdentityState.IDENTITY_PROVISIONING,
        {"simulated": "process_crash"},
    )
    job = await store.start_provisioning_job(
        identity_id=identity["id"],
        company_id=company_id,
        worker_id=worker_id,
        idempotency_key=idempotency_key,
    )
    job["updated_at"] = datetime.now(UTC) - timedelta(seconds=61)

    resumed = await _post(
        client,
        signers["operator"],
        "/v1/worker-identities/provision",
        {
            "company_id": str(company_id),
            "worker_id": str(worker_id),
            "actor": {"actor_id": "orchestrator-api", "purpose": "resume crashed hiring"},
            "idempotency_key": idempotency_key,
        },
    )
    assert resumed.status_code == 200
    assert resumed.json()["state"] == "IDENTITY_VERIFYING"
    assert store.provisioning_jobs[idempotency_key]["attempt_count"] == 2


@pytest.mark.anyio
async def test_worker_cannot_read_another_workers_mailbox(identity_client):
    client, signers = identity_client
    company_id, worker_a, worker_b = uuid4(), uuid4(), uuid4()
    for worker in (worker_a, worker_b):
        provision = {"company_id": str(company_id), "worker_id": str(worker), "actor": {"actor_id": "orchestrator-api", "purpose": "approved hiring"}, "idempotency_key": f"mailbox:{company_id}:{worker}"}
        assert (await _post(client, signers["operator"], "/v1/worker-identities/provision", provision)).status_code == 200
        assert (await _post(client, signers["operator"], f"/v1/worker-identities/{worker}/verify", {"actor": {"actor_id": "orchestrator-api", "purpose": "delivery verification"}, "provider_message_id": f"message-{worker}"})).status_code == 200
    denied = await _post(client, signers["worker-a"], "/v1/mail/list", {"worker_id": str(worker_b), "actor": {"actor_id": str(worker_a), "purpose": "read mail"}})
    assert denied.status_code == 403
    allowed = await _post(client, signers["worker-a"], "/v1/mail/list", {"worker_id": str(worker_a), "actor": {"actor_id": str(worker_a), "purpose": "read mail"}})
    assert allowed.status_code == 200


@pytest.mark.anyio
async def test_signed_request_replay_and_secret_response_are_rejected(identity_client):
    client, signers = identity_client
    body = {"cursor": 0, "limit": 10}
    raw = json.dumps(body, separators=(",", ":"), sort_keys=True).encode()
    headers = signers["operator"].sign_headers("POST", "/v1/sync/events", raw)
    assert (await _post(client, signers["operator"], "/v1/sync/events", body, headers=headers)).status_code == 200
    assert (await _post(client, signers["operator"], "/v1/sync/events", body, headers=headers)).status_code == 401
    store = client._transport.app.state.identity_store  # type: ignore[attr-defined]
    for number in range(7):
        await store.create_outbox("test.event", "test", str(number), {"number": number})
    acknowledged = await _post(client, signers["operator"], "/v1/sync/ack", {"cursor": 7})
    assert acknowledged.status_code == 200
    assert acknowledged.json()["acknowledged_cursor"] == 7
    assert await store.get_client_cursor("operator") == 7
    assert (await store.get_client_registration("operator"))["public_key"] == signers["operator"].public_key_base64()
    store.client_registrations["operator"]["state"] = "REVOKED"
    revoked = await _post(client, signers["operator"], "/v1/sync/events", {"cursor": 7, "limit": 10})
    assert revoked.status_code == 401
    health = await client.get("/healthz")
    assert "secret" not in json.dumps(health.json()).lower()


@pytest.mark.anyio
async def test_outbound_requires_human_approval(identity_client):
    client, signers = identity_client
    company_id, worker = uuid4(), uuid4()
    provision = {"company_id": str(company_id), "worker_id": str(worker), "actor": {"actor_id": "orchestrator-api", "purpose": "approved hiring"}, "idempotency_key": f"mailbox:{company_id}:{worker}"}
    await _post(client, signers["operator"], "/v1/worker-identities/provision", provision)
    await _post(client, signers["operator"], f"/v1/worker-identities/{worker}/verify", {"actor": {"actor_id": "orchestrator-api", "purpose": "delivery verification"}, "provider_message_id": "message"})
    request = await _post(client, signers["worker-a"], "/v1/outbound/request", {"worker_id": str(worker), "actor": {"actor_id": str(worker), "purpose": "external contact"}, "idempotency_key": f"outbound:{worker}:1", "recipients": ["recipient@example.net"], "subject": "Hello", "body": "safe body", "recipient_class": "approved_external"})
    assert request.status_code == 200
    request_payload = request.json()
    assert "body" not in json.dumps(request_payload).lower()
    denied = await _post(client, signers["worker-a"], "/v1/outbound/send-approved", {"worker_id": str(worker), "actor": {"actor_id": str(worker), "purpose": "external contact"}, "outbound_request_id": request_payload["request"]["id"], "idempotency_key": f"submit:{worker}:1"})
    assert denied.status_code == 403
    approval = request_payload["approval"]["id"]
    assert (await _post(client, signers["operator"], f"/v1/approvals/{approval}/decision", {"actor": {"actor_id": "operator", "purpose": "human approval"}, "approved": True})).status_code == 200
    submit_body = {"worker_id": str(worker), "actor": {"actor_id": str(worker), "purpose": "external contact"}, "outbound_request_id": request_payload["request"]["id"], "idempotency_key": f"submit:{worker}:1"}
    submit_raw = json.dumps(submit_body, separators=(",", ":"), sort_keys=True).encode()
    submit_headers = signers["worker-a"].sign_headers("POST", "/v1/outbound/send-approved", submit_raw)
    submit_headers["X-AIAT-Trace-ID"] = "trace-mail-001"
    sent = await _post(client, signers["worker-a"], "/v1/outbound/send-approved", submit_body, headers=submit_headers)
    assert sent.status_code == 200
    assert sent.json()["state"] == "SUBMITTED"
    repeated = await _post(client, signers["worker-a"], "/v1/outbound/send-approved", {"worker_id": str(worker), "actor": {"actor_id": str(worker), "purpose": "external contact"}, "outbound_request_id": request_payload["request"]["id"], "idempotency_key": f"submit:{worker}:1"})
    assert repeated.status_code == 200 and repeated.json()["state"] == "SUBMITTED"
    assert client._transport.app.state.identity_service.stalwart.submission_count == 1  # type: ignore[attr-defined]
    store = client._transport.app.state.identity_store  # type: ignore[attr-defined]
    assert store.delivery_attempts[-1]["outcome"] == "QUEUED"
    assert store.delivery_attempts[-1]["provider_correlation_id"] == "provider-submit"
    assert store.delivery_attempts[-1]["trace_id"] == "trace-mail-001"
    assert len(store.delivery_attempts[-1]["span_id"]) == 16
    assert "recipient@example.net" not in str(store.delivery_attempts[-1])


@pytest.mark.anyio
async def test_verified_provider_webhook_is_idempotent_payload_free_and_projected(identity_client):
    client, signers = identity_client
    worker_id = uuid4()
    body = {
        "provider": "resend",
        "payload": {
            "id": "provider-event-1",
            "type": "email.bounced",
            "created_at": "2026-08-17T12:00:00Z",
            "data": {
                "email_id": "provider-message-1",
                "status": "bounced",
                "reason_code": "550",
                "to": "recipient@example.net",
                "body": "must-not-persist",
            },
        },
        "worker_id": str(worker_id),
        "signature_verified": True,
        "actor": {"actor_id": "orchestrator-api", "purpose": "persist verified provider event"},
        "trace_id": "trace-mail-edge-001",
    }
    first = await _post(client, signers["operator"], "/v1/mail-edge/provider-webhook", body)
    repeated = await _post(client, signers["operator"], "/v1/mail-edge/provider-webhook", body)
    assert first.status_code == repeated.status_code == 200
    assert first.json()["event_type"] == "bounced"
    assert first.json()["failure_class"] == "permanent"
    assert first.json()["id"] == repeated.json()["id"]
    assert "must-not-persist" not in first.text
    assert "recipient@example.net" not in first.text
    assert first.json()["metadata"] == {
        "provider_event_type": "email.bounced",
        "provider_reason_code": "550",
        "provider_status": "bounced",
    }

    store = client._transport.app.state.identity_store  # type: ignore[attr-defined]
    assert len(store.mail_edge_observations) == 1
    assert "must-not-persist" not in str(store.mail_edge_observations)
    projection = await store.dashboard_rows("mail-relay")
    assert projection[-1]["source"] == "provider_webhook"
    assert projection[-1]["event_type"] == "bounced"
    assert projection[-1]["trace_id"] == "trace-mail-edge-001"

    conflict = {**body, "payload": {**body["payload"], "type": "email.delivered"}}
    conflicting = await _post(client, signers["operator"], "/v1/mail-edge/provider-webhook", conflict)
    assert conflicting.status_code == 409
    unsigned = {**body, "signature_verified": False, "payload": {**body["payload"], "id": "provider-event-2"}}
    rejected = await _post(client, signers["operator"], "/v1/mail-edge/provider-webhook", unsigned)
    assert rejected.status_code == 403


@pytest.mark.anyio
async def test_mailbox_grant_and_external_credential_lease_are_durable_and_secret_free(identity_client):
    client, signers = identity_client
    company_id, worker = uuid4(), uuid4()
    provision = {
        "company_id": str(company_id), "worker_id": str(worker),
        "actor": {"actor_id": "orchestrator-api", "purpose": "approved hiring"},
        "idempotency_key": f"mailbox:{company_id}:{worker}",
    }
    assert (await _post(client, signers["operator"], "/v1/worker-identities/provision", provision)).status_code == 200
    assert (await _post(client, signers["operator"], f"/v1/worker-identities/{worker}/verify", {
        "actor": {"actor_id": "orchestrator-api", "purpose": "delivery verification"},
        "provider_message_id": "message",
    })).status_code == 200

    store = client._transport.app.state.identity_store  # type: ignore[attr-defined]
    assert store.identity_grants
    store.identity_grants.clear()
    denied = await _post(client, signers["worker-a"], "/v1/mail/list", {
        "worker_id": str(worker), "actor": {"actor_id": str(worker), "purpose": "read mail"},
    })
    assert denied.status_code == 403
    assert (await _post(client, signers["operator"], "/v1/worker-identities/provision", provision)).status_code == 200

    account = await _post(client, signers["worker-a"], "/v1/external-accounts/signup-request", {
        "worker_id": str(worker), "actor": {"actor_id": str(worker), "purpose": "approved test account"},
        "service": "github", "service_category": "development_test",
        "idempotency_key": f"account:{worker}:github",
    })
    assert account.status_code == 200
    account_payload = account.json()
    assert account_payload["state"] == "ACTIVE"
    assert account_payload["approval_id"]
    assert account_payload["credential_ref"] == "[REDACTED]"
    stored_account = store.external_accounts[UUID(account_payload["id"])]
    assert stored_account["credential_ref"].startswith("external-credential-")
    repeated_account = await _post(client, signers["worker-a"], "/v1/external-accounts/signup-request", {
        "worker_id": str(worker), "actor": {"actor_id": str(worker), "purpose": "approved test account"},
        "service": "github", "service_category": "development_test",
        "idempotency_key": f"account:{worker}:github",
    })
    assert repeated_account.status_code == 200
    assert repeated_account.json()["id"] == account_payload["id"]
    assert repeated_account.json()["approval_id"] == account_payload["approval_id"]
    session = await _post(client, signers["worker-a"], "/v1/sessions/create", {
        "worker_id": str(worker), "actor": {"actor_id": str(worker), "purpose": "local browser login"},
        "service": "github", "external_account_id": account_payload["id"],
        "idempotency_key": f"session:{worker}:github",
    })
    assert session.status_code == 200
    assert not {"lease_token", "lease_hash", "credential_ref", "token", "cookie"} & set(session.json())
    lease_response = await _post(client, signers["worker-a"], "/v1/sessions/lease", {
        "worker_id": str(worker), "actor": {"actor_id": str(worker), "purpose": "local browser broker"},
        "session_id": session.json()["id"],
    })
    assert lease_response.status_code == 200
    lease_token = lease_response.json()["lease_token"]
    used = await _post(client, signers["worker-a"], "/v1/sessions/use", {
        "worker_id": str(worker), "actor": {"actor_id": str(worker), "purpose": "local browser broker"},
        "session_id": session.json()["id"], "lease_token": lease_token,
    })
    assert used.status_code == 200 and "lease_token" not in used.json()
    replayed = await _post(client, signers["worker-a"], "/v1/sessions/use", {
        "worker_id": str(worker), "actor": {"actor_id": str(worker), "purpose": "local browser broker"},
        "session_id": session.json()["id"], "lease_token": lease_token,
    })
    assert replayed.status_code == 403
    assert len(store.credential_leases) == 1
    lease = next(iter(store.credential_leases.values()))
    assert "lease_hash" in lease and "lease_token" not in lease and lease["state"] == "CONSUMED"
    assert {event["kind"] for event in store.usage_events} >= {
        "mailbox_provisioning", "mailbox_storage_mb", "signup_attempt", "browser_minute"
    }


@pytest.mark.anyio
async def test_external_account_high_risk_actions_pause_for_human_policy(identity_client):
    client, signers = identity_client
    policy_path = "/v1/external-accounts/action-policy"
    policy = await client.get(policy_path, headers=signers["operator"].sign_headers("GET", policy_path, b""))
    assert policy.status_code == 200
    assert policy.json()["schema_version"] == "aiat.external-account-action-policy.v1"
    actions = {item["action"]: item for item in policy.json()["actions"]}
    assert actions["rotate_credentials"]["approval_required"] is True
    assert actions["close"]["approval_kind"] == "external_account_close"
    assert actions["suspend"]["approval_required"] is False

    company_id, worker = uuid4(), uuid4()
    provision = await _post(client, signers["operator"], "/v1/worker-identities/provision", {
        "company_id": str(company_id), "worker_id": str(worker),
        "actor": {"actor_id": "orchestrator-api", "purpose": "approved hiring"},
        "idempotency_key": f"mailbox:{company_id}:{worker}",
    })
    verify = await _post(client, signers["operator"], f"/v1/worker-identities/{worker}/verify", {
        "actor": {"actor_id": "orchestrator-api", "purpose": "delivery verification"},
        "provider_message_id": "high-risk-policy-message",
    })
    assert provision.status_code == 200, provision.text
    assert verify.status_code == 200, verify.text
    account = await _post(client, signers["worker-a"], "/v1/external-accounts/signup-request", {
        "worker_id": str(worker), "actor": {"actor_id": str(worker), "purpose": "approved test account"},
        "service": "github", "service_category": "development_test",
        "idempotency_key": f"account:{worker}:high-risk",
    })
    assert account.status_code == 200
    account_id = account.json()["id"]
    session = await _post(client, signers["worker-a"], "/v1/sessions/create", {
        "worker_id": str(worker), "actor": {"actor_id": str(worker), "purpose": "local browser login"},
        "service": "github", "external_account_id": account_id,
        "idempotency_key": f"session:{worker}:high-risk",
    })
    lease = await _post(client, signers["worker-a"], "/v1/sessions/lease", {
        "worker_id": str(worker), "actor": {"actor_id": str(worker), "purpose": "local browser broker"},
        "session_id": session.json()["id"],
    })

    close_path = f"/v1/external-accounts/{account_id}/close"
    pending = await _post(client, signers["worker-a"], close_path, {
        "worker_id": str(worker), "actor": {"actor_id": str(worker), "purpose": "close external account"},
        "service": "github", "service_category": "development_test",
        "idempotency_key": f"close:{worker}:high-risk",
    })
    assert pending.status_code == 200
    assert pending.json()["state"] == "PENDING_APPROVAL"
    approval_id = pending.json()["approval"]["id"]
    store = client._transport.app.state.identity_store  # type: ignore[attr-defined]
    assert str(store.external_accounts[UUID(account_id)]["state"]) == "ACTIVE"

    decided = await _post(client, signers["operator"], f"/v1/approvals/{approval_id}/decision", {
        "actor": {"actor_id": "operator", "purpose": "human external-account closure approval"},
        "approved": True,
    })
    assert decided.status_code == 200
    assert str(store.external_accounts[UUID(account_id)]["state"]) == "CLOSED"
    denied = await _post(client, signers["worker-a"], "/v1/sessions/use", {
        "worker_id": str(worker), "actor": {"actor_id": str(worker), "purpose": "closed account must be revoked"},
        "session_id": session.json()["id"], "lease_token": lease.json()["lease_token"],
    })
    assert denied.status_code == 403


@pytest.mark.anyio
async def test_external_account_suspension_immediately_revokes_issued_browser_lease(
    identity_client,
) -> None:
    client, signers = identity_client
    company_id, worker = uuid4(), uuid4()
    await _post(client, signers["operator"], "/v1/worker-identities/provision", {
        "company_id": str(company_id), "worker_id": str(worker),
        "actor": {"actor_id": "orchestrator-api", "purpose": "approved hiring"},
        "idempotency_key": f"mailbox:{company_id}:{worker}",
    })
    await _post(client, signers["operator"], f"/v1/worker-identities/{worker}/verify", {
        "actor": {"actor_id": "orchestrator-api", "purpose": "delivery verification"},
        "provider_message_id": "suspension-message",
    })
    account = await _post(client, signers["worker-a"], "/v1/external-accounts/signup-request", {
        "worker_id": str(worker), "actor": {"actor_id": str(worker), "purpose": "approved test account"},
        "service": "github", "service_category": "development_test",
        "idempotency_key": f"account:{worker}:suspension",
    })
    account_id = account.json()["id"]
    session = await _post(client, signers["worker-a"], "/v1/sessions/create", {
        "worker_id": str(worker), "actor": {"actor_id": str(worker), "purpose": "local browser login"},
        "service": "github", "external_account_id": account_id,
        "idempotency_key": f"session:{worker}:suspension",
    })
    lease = await _post(client, signers["worker-a"], "/v1/sessions/lease", {
        "worker_id": str(worker), "actor": {"actor_id": str(worker), "purpose": "local browser broker"},
        "session_id": session.json()["id"],
    })
    suspended = await _post(
        client,
        signers["worker-a"],
        f"/v1/external-accounts/{account_id}/suspend",
        {
            "worker_id": str(worker),
            "actor": {"actor_id": str(worker), "purpose": "suspend external account"},
            "service": "github",
            "service_category": "development_test",
            "idempotency_key": f"suspend-account:{worker}:github",
        },
    )
    assert suspended.status_code == 200
    denied = await _post(client, signers["worker-a"], "/v1/sessions/use", {
        "worker_id": str(worker), "actor": {"actor_id": str(worker), "purpose": "must be revoked"},
        "session_id": session.json()["id"], "lease_token": lease.json()["lease_token"],
    })
    assert denied.status_code == 403


@pytest.mark.anyio
async def test_provider_outage_cannot_prevent_local_worker_identity_revocation(
    identity_client,
) -> None:
    client, signers = identity_client
    company_id, worker = uuid4(), uuid4()
    await _post(client, signers["operator"], "/v1/worker-identities/provision", {
        "company_id": str(company_id), "worker_id": str(worker),
        "actor": {"actor_id": "orchestrator-api", "purpose": "approved hiring"},
        "idempotency_key": f"mailbox:{company_id}:{worker}",
    })
    await _post(client, signers["operator"], f"/v1/worker-identities/{worker}/verify", {
        "actor": {"actor_id": "orchestrator-api", "purpose": "delivery verification"},
        "provider_message_id": "provider-outage-message",
    })
    account = await _post(client, signers["worker-a"], "/v1/external-accounts/signup-request", {
        "worker_id": str(worker), "actor": {"actor_id": str(worker), "purpose": "approved test account"},
        "service": "github", "service_category": "development_test",
        "idempotency_key": f"account:{worker}:provider-outage",
    })
    session = await _post(client, signers["worker-a"], "/v1/sessions/create", {
        "worker_id": str(worker), "actor": {"actor_id": str(worker), "purpose": "local browser login"},
        "service": "github", "external_account_id": account.json()["id"],
        "idempotency_key": f"session:{worker}:provider-outage",
    })

    class UnavailableDisableProvider(FakeStalwart):
        async def disable_mailbox(self, *_args):
            raise RuntimeError("simulated provider outage")

    provider = UnavailableDisableProvider()
    service = client._transport.app.state.identity_service  # type: ignore[attr-defined]
    service.stalwart = provider
    service.mailboxes.provider = provider
    with pytest.raises(RuntimeError, match="simulated provider outage"):
        await _post(
            client,
            signers["operator"],
            f"/v1/worker-identities/{worker}/suspend",
            {
                "actor": {
                    "actor_id": "orchestrator-api",
                    "purpose": "worker deactivated",
                },
            },
        )

    store = client._transport.app.state.identity_store  # type: ignore[attr-defined]
    assert str(store.identities_by_worker[worker]["state"]) == "SUSPENDED"
    assert store.sessions[UUID(session.json()["id"])]["state"] == "REVOKED"
    assert store.external_accounts[UUID(account.json()["id"])]["state"] == "SUSPENDED"
    assert store.audit[-1]["outcome"] == "local_revoked_provider_pending"


@pytest.mark.anyio
async def test_temporary_mailbox_waits_for_human_approval_and_suspension_revokes_access(identity_client):
    client, signers = identity_client
    company_id, worker = uuid4(), uuid4()
    provision = {
        "company_id": str(company_id), "worker_id": str(worker),
        "actor": {"actor_id": "orchestrator-api", "purpose": "approved hiring"},
        "idempotency_key": f"mailbox:{company_id}:{worker}", "mailbox_class": "temporary",
    }
    pending = await _post(client, signers["operator"], "/v1/worker-identities/provision", provision)
    assert pending.status_code == 200
    assert pending.json()["state"] == "TEMPORARY_MAILBOX_APPROVAL_PENDING"
    store = client._transport.app.state.identity_store  # type: ignore[attr-defined]
    assert store.identities_by_worker[worker]["provider_account_id"] is None
    approval = next(item for item in store.approvals.values() if item["kind"] == "temporary_mailbox")
    assert (await _post(client, signers["operator"], f"/v1/approvals/{approval['id']}/decision", {
        "actor": {"actor_id": "operator", "purpose": "human temporary mailbox approval"},
        "approved": True,
    })).status_code == 200
    assert (await _post(client, signers["operator"], "/v1/worker-identities/provision", provision)).json()["state"] == "IDENTITY_VERIFYING"
    assert (await _post(client, signers["operator"], f"/v1/worker-identities/{worker}/verify", {
        "actor": {"actor_id": "orchestrator-api", "purpose": "delivery verification"},
        "provider_message_id": "temporary-message",
    })).status_code == 200
    suspended = await _post(client, signers["operator"], f"/v1/worker-identities/{worker}/suspend", {
        "actor": {"actor_id": "orchestrator-api", "purpose": "worker deactivated"},
    })
    assert suspended.status_code == 200, suspended.text
    assert suspended.json()["state"] == "SUSPENDED"
    denied = await _post(client, signers["worker-a"], "/v1/mail/read", {
        "worker_id": str(worker), "actor": {"actor_id": str(worker), "purpose": "read mail"},
        "message_id": "temporary-message",
    })
    assert denied.status_code == 403
    archived = await _post(client, signers["operator"], f"/v1/worker-identities/{worker}/archive", {
        "actor": {"actor_id": "orchestrator-api", "purpose": "worker retired"},
    })
    assert archived.status_code == 200
    assert archived.json()["state"] == "ARCHIVED"


def test_production_policy_rejects_direct_mx_and_missing_crypto() -> None:
    with pytest.raises(ValueError, match="DIRECT_MX_OUTBOUND_ENABLED"):
        IdentitySettings(direct_mx_outbound_enabled=True)
    with pytest.raises(ValueError, match="missing required production identity configuration"):
        IdentitySettings(MAS_ENVIRONMENT="production")


def test_development_profile_can_disable_all_external_relay_paths() -> None:
    settings = IdentitySettings(
        MAS_ENVIRONMENT="development",
        outbound_relay_provider="disabled",
        outbound_relay_host="",
        outbound_relay_port=0,
        outbound_relay_tls_mode="disabled",
    )
    assert settings.outbound_relay_provider == "disabled"
    assert settings.direct_mx_outbound_enabled is False
    assert settings.default_outbound_enabled is False
    assert settings.outbound_relay_certified is False


def test_profile_domains_are_explicit_and_isolated() -> None:
    development = IdentitySettings(
        IDENTITY_PROFILE="development",
        MAS_ENVIRONMENT="development",
        agent_mail_domain="agents.aiat.local",
    )
    assert development.agent_mail_domain == "agents.aiat.local"
    with pytest.raises(ValueError, match="agents.aiat.ca"):
        IdentitySettings(
            IDENTITY_PROFILE="production",
            MAS_ENVIRONMENT="production",
            agent_mail_domain="agents.aiat.local",
            mail_hostname="mail.localhost",
        )


@pytest.mark.anyio
async def test_client_scope_reduction_cannot_leave_stale_durable_authority() -> None:
    signer = SignedClient.from_base64("operator", _private_key_b64())
    store = InMemoryIdentityStore()
    await store.ensure_client_registration(
        client_id="operator",
        public_key=signer.public_key_base64(),
        scopes=["identity:admin"],
    )
    app = create_app(
        settings=IdentitySettings(
            identity_client_public_keys_json=json.dumps({
                "operator": signer.public_key_base64(),
            }),
            identity_client_scopes_json=json.dumps({"operator": []}),
        ),
        store=store,
    )
    with pytest.raises(RuntimeError, match="registration mismatch"):
        async with app.router.lifespan_context(app):
            pass


@pytest.mark.anyio
async def test_unknown_external_service_category_is_default_denied(identity_client):
    client, signers = identity_client
    company_id, worker = uuid4(), uuid4()
    await _post(client, signers["operator"], "/v1/worker-identities/provision", {
        "company_id": str(company_id), "worker_id": str(worker),
        "actor": {"actor_id": "orchestrator-api", "purpose": "approved hiring"},
        "idempotency_key": f"mailbox:{company_id}:{worker}",
    })
    await _post(client, signers["operator"], f"/v1/worker-identities/{worker}/verify", {
        "actor": {"actor_id": "orchestrator-api", "purpose": "delivery verification"},
        "provider_message_id": "unknown-category-message",
    })
    response = await _post(client, signers["worker-a"], "/v1/external-accounts/signup-request", {
        "worker_id": str(worker), "actor": {"actor_id": str(worker), "purpose": "unknown provider"},
        "service": "unreviewed.example", "service_category": "unreviewed_category",
        "idempotency_key": f"unknown:{worker}",
    })
    assert response.status_code == 403


@pytest.mark.anyio
async def test_mail_provider_rate_limit_is_enforced(identity_client):
    client, signers = identity_client
    company_id, worker = uuid4(), uuid4()
    provision = {
        "company_id": str(company_id), "worker_id": str(worker),
        "actor": {"actor_id": "orchestrator-api", "purpose": "approved hiring"},
        "idempotency_key": f"mailbox:{company_id}:{worker}",
    }
    await _post(client, signers["operator"], "/v1/worker-identities/provision", provision)
    await _post(client, signers["operator"], f"/v1/worker-identities/{worker}/verify", {
        "actor": {"actor_id": "orchestrator-api", "purpose": "delivery verification"},
        "provider_message_id": "message-rate-limit",
    })
    client._transport.app.state.identity_service.settings.provider_rate_limit_per_minute = 1  # type: ignore[attr-defined]
    body = {"worker_id": str(worker), "actor": {"actor_id": str(worker), "purpose": "read mail"}}
    assert (await _post(client, signers["worker-a"], "/v1/mail/list", body)).status_code == 200
    assert (await _post(client, signers["worker-a"], "/v1/mail/list", body)).status_code == 403


@pytest.mark.anyio
async def test_external_account_cannot_bind_another_workers_email_identity(identity_client):
    client, signers = identity_client
    company_id, worker_a, worker_b = uuid4(), uuid4(), uuid4()
    identities: dict = {}
    for worker in (worker_a, worker_b):
        provision = await _post(client, signers["operator"], "/v1/worker-identities/provision", {
            "company_id": str(company_id), "worker_id": str(worker),
            "actor": {"actor_id": "orchestrator-api", "purpose": "approved hiring"},
            "idempotency_key": f"mailbox:{company_id}:{worker}",
        })
        identities[worker] = provision.json()["id"]
        await _post(client, signers["operator"], f"/v1/worker-identities/{worker}/verify", {
            "actor": {"actor_id": "orchestrator-api", "purpose": "delivery verification"},
            "provider_message_id": f"message-{worker}",
        })
    denied = await _post(client, signers["worker-a"], "/v1/external-accounts/signup-request", {
        "worker_id": str(worker_a),
        "actor": {"actor_id": str(worker_a), "purpose": "cross-worker binding attempt"},
        "service": "github", "service_category": "development_test",
        "email_identity_id": identities[worker_b],
        "idempotency_key": f"account:{worker_a}:cross-worker",
    })
    assert denied.status_code == 403


@pytest.mark.anyio
async def test_browser_session_requires_a_governed_external_account(identity_client):
    client, signers = identity_client
    worker = uuid4()
    response = await _post(client, signers["worker-a"], "/v1/sessions/create", {
        "worker_id": str(worker),
        "actor": {"actor_id": str(worker), "purpose": "ungoverned browser attempt"},
        "service": "unknown.example", "idempotency_key": f"session:{worker}:unknown",
    })
    assert response.status_code == 422


@pytest.mark.anyio
async def test_suspension_blocks_a_previously_approved_outbound_request(identity_client):
    client, signers = identity_client
    company_id, worker = uuid4(), uuid4()
    await _post(client, signers["operator"], "/v1/worker-identities/provision", {
        "company_id": str(company_id), "worker_id": str(worker),
        "actor": {"actor_id": "orchestrator-api", "purpose": "approved hiring"},
        "idempotency_key": f"mailbox:{company_id}:{worker}",
    })
    await _post(client, signers["operator"], f"/v1/worker-identities/{worker}/verify", {
        "actor": {"actor_id": "orchestrator-api", "purpose": "delivery verification"},
        "provider_message_id": "suspend-message",
    })
    outbound = await _post(client, signers["worker-a"], "/v1/outbound/request", {
        "worker_id": str(worker),
        "actor": {"actor_id": str(worker), "purpose": "approved then suspended"},
        "idempotency_key": f"outbound:{worker}:suspend",
        "recipients": ["recipient@example.net"], "subject": "Hello",
        "body": "safe body", "recipient_class": "approved_external",
    })
    payload = outbound.json()
    await _post(client, signers["operator"], f"/v1/approvals/{payload['approval']['id']}/decision", {
        "actor": {"actor_id": "operator", "purpose": "human approval"},
        "approved": True,
    })
    await _post(client, signers["operator"], f"/v1/worker-identities/{worker}/suspend", {
        "actor": {"actor_id": "orchestrator-api", "purpose": "worker deactivated"},
    })
    denied = await _post(client, signers["worker-a"], "/v1/outbound/send-approved", {
        "worker_id": str(worker),
        "actor": {"actor_id": str(worker), "purpose": "must remain denied"},
        "outbound_request_id": payload["request"]["id"],
        "idempotency_key": f"submit:{worker}:suspend",
    })
    assert denied.status_code == 403


@pytest.mark.anyio
async def test_reconciliation_uses_server_ack_and_rejects_cursor_skips(identity_client):
    client, signers = identity_client
    store = client._transport.app.state.identity_store  # type: ignore[attr-defined]
    first = await store.create_outbox("first", "test", "1", {})
    assert (await _post(client, signers["operator"], "/v1/sync/ack", {"cursor": first["sequence"]})).status_code == 200
    second = await store.create_outbox("second", "test", "2", {})
    replay = await _post(client, signers["operator"], "/v1/sync/events", {"cursor": 9999, "limit": 10})
    assert replay.status_code == 200
    assert [event["sequence"] for event in replay.json()["events"]] == [second["sequence"]]
    assert replay.json()["cursor"] == first["sequence"]
    assert (await _post(client, signers["operator"], "/v1/sync/ack", {"cursor": 9999})).status_code == 409


@pytest.mark.anyio
async def test_past_skew_boundary_nonce_remains_replay_protected(monkeypatch):
    signer = SignedClient.from_base64("operator", _private_key_b64())
    monkeypatch.setattr("identity_service.clients.auth.time.time", lambda: 10_000)
    body = {"cursor": 0, "limit": 10}
    raw = json.dumps(body, separators=(",", ":"), sort_keys=True).encode()
    headers = signer.sign_headers(
        "POST", "/v1/sync/events", raw, now=9_700
    )

    class BoundaryReplayStore:
        def __init__(self) -> None:
            self.expires_at: int | None = None

        async def consume_client_nonce(self, _client_id: str, _nonce: str, expires_at: int) -> bool:
            if self.expires_at is not None and self.expires_at > 10_000:
                return False
            self.expires_at = expires_at
            return True

    replay_store = BoundaryReplayStore()
    request = {
        "client_id": "operator", "timestamp": headers["X-AIAT-Timestamp"],
        "nonce": headers["X-AIAT-Nonce"], "signature": headers["X-AIAT-Signature"],
        "method": "POST", "path": "/v1/sync/events", "body": raw,
        "public_keys": {"operator": signer.public_key_base64()},
        "replay_store": replay_store,
    }
    await verify_request(**request)
    assert replay_store.expires_at == 10_300
    with pytest.raises(PermissionError, match="replayed"):
        await verify_request(**request)
