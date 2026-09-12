from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from identity_service.config import IdentitySettings
from identity_service.messages.verification_parser import (
    extract_verification_code,
    extract_verification_link,
    message_text,
)
from identity_service.providers.base import MailProviderError
from identity_service.providers.inbound.cloudflare import (
    CloudflareEdgeFixture,
    CloudflareInboundAdapter,
    normalize_raw_message,
)
from identity_service.providers.resend import ResendRelayAdapter
from identity_service.service import AuthenticatedClient, IdentityService
from identity_service.store import InMemoryIdentityStore


OPERATOR = AuthenticatedClient("operator", frozenset({"identity:admin", "identity:delegate"}))


class FixtureResend:
    provider_name = "resend"

    async def health_check(self) -> dict:
        return {"healthy": True, "provider": self.provider_name}


async def make_service(*, store: InMemoryIdentityStore | None = None):
    edge = CloudflareEdgeFixture(auth_secret="fixture-mail-edge-secret-012345")
    transport = httpx.MockTransport(edge.handle)
    client = httpx.AsyncClient(transport=transport)
    inbound = CloudflareInboundAdapter(
        edge_url="http://mail-edge.test",
        auth_secret=edge.auth_secret,
        client=client,
    )
    service = IdentityService(
        settings=IdentitySettings(outbound_relay_certified=True),
        store=store or InMemoryIdentityStore(),
        inbound_provider=inbound,
        outbound_provider=FixtureResend(),
    )
    return service, edge, client


async def provision(
    service: IdentityService,
    worker_id: UUID,
    company_id: UUID | None = None,
    friendly_alias: str | None = None,
) -> dict:
    company = company_id or uuid4()
    return await service.provision_identity(
        OPERATOR,
        company_id=company,
        worker_id=worker_id,
        actor_id="orchestrator-api",
        friendly_alias=friendly_alias,
        idempotency_key=f"mailbox:{company}:{worker_id}",
    )


def verification_mime(*, message_id: str = "<fixture-verification-1@example.test>") -> bytes:
    return (
        f"From: Verify <no-reply@verification.example>\r\n"
        f"To: spoofed-other@agents.aiat.ca\r\n"
        f"Message-ID: {message_id}\r\n"
        "Subject: Verify your account\r\n"
        "Content-Type: text/html; charset=utf-8\r\n"
        "\r\n"
        "<p>Your code is <b>481516</b>.</p> "
        "<a href=\"https://example.test/verify?code=481516\">verify</a>\r\n"
    ).encode()


def test_cloudflare_normalization_handles_multipart_malformed_and_spoofed_headers() -> None:
    multipart = (
        "From: Sender <sender@example.test>\r\n"
        "To: worker-b@agents.aiat.ca\r\n"
        "Message-ID: <multipart@example.test>\r\n"
        "Subject: Multipart verification\r\n"
        "Content-Type: multipart/alternative; boundary=fixture-boundary\r\n"
        "\r\n"
        "--fixture-boundary\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n\r\n"
        "Your code is 123456.\r\n"
        "--fixture-boundary\r\n"
        "Content-Type: text/html; charset=utf-8\r\n\r\n"
        "<p><a href=\"https://example.test/verify\">Verify</a></p>\r\n"
        "--fixture-boundary--\r\n"
    ).encode()
    normalized = normalize_raw_message(
        multipart,
        message_id="m-multipart",
        envelope_recipient="worker-a@agents.aiat.ca",
    )
    assert normalized["to"] == [{"email": "worker-a@agents.aiat.ca"}]
    assert normalized["bodyValues"]["text"]["value"].startswith("Your code")
    assert "example.test/verify" in normalized["bodyValues"]["html"]["value"]

    malformed = normalize_raw_message(
        b"From: broken\x00sender\r\nContent-Type: multipart/mixed; boundary=missing\r\n\r\nnot complete",
        message_id="m-malformed",
        envelope_recipient="worker-a@agents.aiat.ca",
    )
    assert malformed["to"] == [{"email": "worker-a@agents.aiat.ca"}]
    assert malformed["id"] == "m-malformed"


def test_cloudflare_fixture_accepts_near_limit_and_rejects_oversized_mail() -> None:
    edge = CloudflareEdgeFixture(auth_secret="fixture-mail-edge-secret-012345", max_message_bytes=1024)
    address = "worker-a@agents.aiat.ca"
    edge.registry[address] = {
        "identity_id": "identity-a",
        "worker_id": "worker-a",
        "address": address,
        "provider_reference": "recipient:a",
        "state": "ACTIVE",
    }
    near_limit = b"x" * 1024

    accepted = edge.receive_message(address, near_limit)
    rejected = edge.receive_message(address, b"x" * 1025)

    assert accepted["accepted"] is True
    assert rejected == {"accepted": False, "reason": "message_too_large"}


@pytest.mark.anyio
async def test_cloudflare_edge_authorizes_envelope_recipient_and_persists_raw_in_r2() -> None:
    service, edge, client = await make_service()
    try:
        worker = uuid4()
        identity = await provision(service, worker, friendly_alias="verify")
        assert edge.registry["verify@agents.aiat.ca"]["identity_id"] == str(identity["id"])
        result = edge.receive_message(identity["address"], verification_mime())
        assert result["accepted"] is True
        alias_result = edge.receive_message(
            "verify@agents.aiat.ca",
            verification_mime(message_id="<alias-delivery@example.test>"),
        )
        assert alias_result["accepted"] is True
        message_id = str(result["message_id"])
        assert result["duplicate"] is False
        metadata = edge.d1["messages"][message_id]
        assert "raw_mime" not in metadata
        assert edge.r2[metadata["raw_object_key"]] == verification_mime()
        assert metadata["envelope_recipient"] == identity["address"]
        assert edge.receive_message(identity["address"], verification_mime())["duplicate"] is True
        assert edge.receive_message("unknown@agents.aiat.ca", verification_mime())["reason"] == "unknown_recipient"

        verified = await service.verify_identity(
            OPERATOR,
            worker_id=worker,
            actor_id="orchestrator-api",
            provider_message_id=message_id,
        )
        assert verified["state"] == "IDENTITY_ACTIVE"
        assert edge.registry[identity["address"]]["state"] == "ACTIVE"
        assert any(
            row["envelope_recipient"] == "verify@agents.aiat.ca"
            for row in service.store.inbound_messages.values()
        )
        worker_client = AuthenticatedClient(str(worker))
        listed = await service.mail_list(worker_client, worker_id=worker, actor_id=str(worker), limit=10)
        assert {entry["to"][0]["email"] for entry in listed["result"]["list"]} == {
            identity["address"],
            "verify@agents.aiat.ca",
        }
        read_result = await service.mail_read(
            worker_client, worker_id=worker, actor_id=str(worker), message_id=message_id
        )
        assert extract_verification_code(message_text(read_result)) == "481516"
        assert extract_verification_link(message_text(read_result)) == "https://example.test/verify?code=481516"
        await service.record_verification_extraction(
            worker_client,
            worker_id=worker,
            actor_id=str(worker),
            message_id=message_id,
            code="481516",
        )
        assert edge.d1["messages"][message_id]["protected_until"]
    finally:
        await client.aclose()


@pytest.mark.anyio
async def test_same_message_id_is_scoped_per_recipient_and_suspension_retires_delivery() -> None:
    service, edge, client = await make_service()
    try:
        company = uuid4()
        worker_a, worker_b = uuid4(), uuid4()
        identity_a = await provision(service, worker_a, company)
        identity_b = await provision(service, worker_b, company)
        raw = verification_mime(message_id="<same-message-id@example.test>")
        first = edge.receive_message(identity_a["address"], raw)
        second = edge.receive_message(identity_b["address"], raw)
        assert first["accepted"] and second["accepted"]
        assert first["message_id"] != second["message_id"]
        await service.verify_identity(OPERATOR, worker_id=worker_a, actor_id="orchestrator-api", provider_message_id=str(first["message_id"]))
        await service.verify_identity(OPERATOR, worker_id=worker_b, actor_id="orchestrator-api", provider_message_id=str(second["message_id"]))
        worker_a_client = AuthenticatedClient(str(worker_a))
        with pytest.raises(PermissionError, match="cross-worker"):
            await service.mail_read(worker_a_client, worker_id=worker_b, actor_id=str(worker_a), message_id=str(second["message_id"]))

        await service.suspend_identity(OPERATOR, worker_id=worker_a, actor_id="orchestrator-api", reason="fixture suspension")
        assert edge.receive_message(identity_a["address"], raw)["reason"] == "recipient_inactive"
        await service.archive_identity(OPERATOR, worker_id=worker_b, actor_id="orchestrator-api", reason="fixture retirement")
        assert edge.registry[identity_b["address"]]["state"] == "RETIRED"
        assert edge.receive_message(identity_b["address"], raw)["reason"] == "recipient_inactive"
    finally:
        await client.aclose()


@pytest.mark.anyio
async def test_cloudflare_sync_retries_after_local_commit_or_ack_cursor_crash() -> None:
    class AckFailsOnce(CloudflareInboundAdapter):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.failed = False

        async def acknowledge_event(self, event_id: str):
            if not self.failed:
                self.failed = True
                raise MailProviderError("EDGE_ACK_UNAVAILABLE", "fixture ack unavailable", transient=True)
            return await super().acknowledge_event(event_id)

    service, edge, client = await make_service()
    try:
        worker = uuid4()
        identity = await provision(service, worker)
        edge.receive_message(identity["address"], verification_mime())
        original = service.inbound_provider
        failing = AckFailsOnce(
            edge_url="http://mail-edge.test", auth_secret=edge.auth_secret, client=client
        )
        service.inbound_provider = failing
        service.mailboxes.provider = failing
        with pytest.raises(MailProviderError, match="fixture ack unavailable"):
            await service.synchronize_inbound()
        assert await service.store.get_inbound_sync_cursor("cloudflare") == 0
        retried = await service.synchronize_inbound()
        assert retried["processed"] == 1
        assert await service.store.get_inbound_sync_cursor("cloudflare") == 1
        assert len(service.store.inbound_messages) == 1
        service.inbound_provider = original
    finally:
        await client.aclose()


@pytest.mark.anyio
async def test_cloudflare_retention_keeps_protected_verification_and_removes_expired_raw_body() -> None:
    service, edge, client = await make_service()
    try:
        worker = uuid4()
        identity = await provision(service, worker)
        raw = verification_mime()
        received = edge.receive_message(identity["address"], raw)
        message_id = str(received["message_id"])
        now = datetime.now(UTC)
        edge.d1["messages"][message_id]["received_at"] = (now - timedelta(days=8)).isoformat()
        edge.d1["messages"][message_id]["protected_until"] = (now + timedelta(hours=1)).isoformat()
        assert edge.cleanup(now=now, retention_days=7, processed_retention_days=1) == 0
        assert edge.r2
        edge.d1["messages"][message_id]["protected_until"] = (now - timedelta(seconds=1)).isoformat()
        assert edge.cleanup(now=now, retention_days=7, processed_retention_days=1) == 1
        assert not edge.r2
        assert edge.d1["messages"][message_id]["deleted_at"]
    finally:
        await client.aclose()


@pytest.mark.anyio
async def test_direct_resend_send_keeps_approval_and_provider_idempotency_controls() -> None:
    calls: list[httpx.Request] = []

    async def resend_handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"id": "resend-message-1"})

    service, edge, edge_client = await make_service()
    resend_client = httpx.AsyncClient(transport=httpx.MockTransport(resend_handler))
    service.outbound_provider = ResendRelayAdapter(
        api_key="fixture-resend-api-key",
        sending_domain="agents.aiat.ca",
        client=resend_client,
    )
    service.outbound.provider = service.outbound_provider
    service.outbound.provider_name = "resend"
    try:
        worker = uuid4()
        identity = await provision(service, worker)
        received = edge.receive_message(identity["address"], verification_mime())
        await service.verify_identity(OPERATOR, worker_id=worker, actor_id="orchestrator-api", provider_message_id=str(received["message_id"]))
        worker_client = AuthenticatedClient(str(worker))
        request, approval = await service.request_outbound(
            worker_client,
            worker_id=worker,
            actor_id=str(worker),
            recipients=["recipient@example.net"],
            subject="HTML fixture",
            body="<strong>safe</strong>",
            recipient_class="approved_external",
            idempotency_key=f"outbound:{worker}:fixture",
            content_type="text/html",
        )
        with pytest.raises(PermissionError, match="approval"):
            await service.send_approved(worker_client, worker_id=worker, actor_id=str(worker), request_id=request["id"], idempotency_key="submit:blocked")
        await service.decide_approval(OPERATOR, approval_id=approval["id"], actor_id="operator", approved=True, reason="fixture approval")
        sent = await service.send_approved(worker_client, worker_id=worker, actor_id=str(worker), request_id=request["id"], idempotency_key="submit:approved")
        repeated = await service.send_approved(worker_client, worker_id=worker, actor_id=str(worker), request_id=request["id"], idempotency_key="submit:approved")
        assert sent["provider_message_id"] == repeated["provider_message_id"] == "resend-message-1"
        assert len(calls) == 1
        assert calls[0].headers["Idempotency-Key"] == "submit:approved"
        json_body = calls[0].content
        assert json_body
        assert b"<strong>safe</strong>" in json_body
    finally:
        await resend_client.aclose()
        await edge_client.aclose()
