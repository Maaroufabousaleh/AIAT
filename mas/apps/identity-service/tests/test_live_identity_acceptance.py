"""Opt-in self-hosted Stalwart/Resend staging acceptance.

This module never runs during ordinary CI. It mutates only an explicitly
approved staging identity service and requires pre-enrolled operator and
worker test signing keys. Environment variable names are documented in the
mail-edge runbook; values must come from the operator secret store.
"""

from __future__ import annotations

import json
import os
import smtplib
from email.message import EmailMessage
from typing import Any
from uuid import UUID, uuid4

import anyio
import httpx
import pytest
from identity_service.clients.auth import SignedClient

pytestmark = pytest.mark.live


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        pytest.fail(f"{name} is required for live identity certification")
    return value


def _live_enabled() -> bool:
    return os.getenv("AIAT_RUN_LIVE_IDENTITY_TESTS", "").strip() == "1"


def _signer(client_id_name: str, private_key_name: str) -> SignedClient:
    return SignedClient.from_base64(_required(client_id_name), _required(private_key_name))


async def _post(
    client: httpx.AsyncClient,
    signer: SignedClient,
    path: str,
    body: dict[str, Any],
) -> httpx.Response:
    raw = json.dumps(body, separators=(",", ":"), sort_keys=True).encode()
    return await client.post(
        path,
        content=raw,
        headers={
            "Content-Type": "application/json",
            **signer.sign_headers("POST", path, raw),
        },
    )


def _send_inbound(
    *,
    hostname: str,
    port: int,
    envelope_from: str,
    recipient: str,
    subject: str,
    code: str,
) -> None:
    message = EmailMessage()
    message["From"] = envelope_from
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(f"AIAT staging certification code: {code}")
    with smtplib.SMTP(hostname, port, timeout=30) as smtp:
        smtp.ehlo()
        smtp.send_message(message, from_addr=envelope_from, to_addrs=[recipient])


def _unknown_recipient_is_rejected(
    *, hostname: str, port: int, envelope_from: str, domain: str
) -> bool:
    unknown = f"unknown-certification-{uuid4()}@{domain}"
    with smtplib.SMTP(hostname, port, timeout=30) as smtp:
        smtp.ehlo()
        mail_code, _ = smtp.mail(envelope_from)
        if mail_code >= 400:
            raise AssertionError("Stalwart rejected the certification envelope sender")
        rcpt_code, _ = smtp.rcpt(unknown)
        return rcpt_code >= 500


async def _find_message(
    client: httpx.AsyncClient,
    signer: SignedClient,
    *,
    worker_id: UUID,
    subject: str,
    timeout_seconds: int = 90,
) -> str:
    deadline = anyio.current_time() + timeout_seconds
    body = {
        "worker_id": str(worker_id),
        "actor": {
            "actor_id": str(worker_id),
            "purpose": "live inbound certification",
        },
        "query": subject,
        "limit": 10,
    }
    while anyio.current_time() < deadline:
        response = await _post(client, signer, "/v1/mail/search", body)
        assert response.status_code == 200, response.text
        message_ids = ((response.json().get("result") or {}).get("ids") or [])
        if message_ids:
            return str(message_ids[0])
        await anyio.sleep(2)
    raise AssertionError("live Stalwart message was not visible through governed JMAP access")


@pytest.mark.anyio
async def test_live_self_hosted_stalwart_and_resend_acceptance() -> None:
    if not _live_enabled():
        pytest.skip("set AIAT_RUN_LIVE_IDENTITY_TESTS=1 on approved staging only")

    identity_url = _required("LIVE_IDENTITY_SERVICE_URL").rstrip("/")
    mail_host = _required("LIVE_MAIL_HOST")
    mail_port = int(os.getenv("LIVE_SMTP_PORT", "25"))
    envelope_from = _required("LIVE_SMTP_ENVELOPE_FROM")
    outbound_recipient = _required("LIVE_IDENTITY_OUTBOUND_RECIPIENT")
    if os.getenv("LIVE_IDENTITY_REQUIRE_REPLY", "").strip() != "1":
        pytest.fail("LIVE_IDENTITY_REQUIRE_REPLY=1 is required for reply-path certification")
    if os.getenv("LIVE_IDENTITY_SUSPEND_WORKER_B", "").strip() != "1":
        pytest.fail(
            "LIVE_IDENTITY_SUSPEND_WORKER_B=1 is required for revocation certification"
        )
    company_id = UUID(_required("LIVE_IDENTITY_COMPANY_ID"))
    worker_a = UUID(_required("LIVE_IDENTITY_WORKER_A_ID"))
    worker_b = UUID(_required("LIVE_IDENTITY_WORKER_B_ID"))
    operator = _signer(
        "LIVE_IDENTITY_OPERATOR_CLIENT_ID",
        "LIVE_IDENTITY_OPERATOR_PRIVATE_KEY",
    )
    worker_signers = {
        worker_a: _signer(
            "LIVE_IDENTITY_WORKER_A_CLIENT_ID",
            "LIVE_IDENTITY_WORKER_A_PRIVATE_KEY",
        ),
        worker_b: _signer(
            "LIVE_IDENTITY_WORKER_B_CLIENT_ID",
            "LIVE_IDENTITY_WORKER_B_PRIVATE_KEY",
        ),
    }
    assert worker_signers[worker_a].client_id in {
        str(worker_a),
        f"worker:{worker_a}",
    }
    assert worker_signers[worker_b].client_id in {
        str(worker_b),
        f"worker:{worker_b}",
    }

    async with httpx.AsyncClient(
        base_url=identity_url,
        timeout=30,
        follow_redirects=False,
    ) as client:
        assert (await client.get("/healthz")).status_code == 200
        assert (await client.get("/readyz")).status_code == 200

        identities: dict[UUID, dict[str, Any]] = {}
        for worker_id in (worker_a, worker_b):
            provision_body = {
                "company_id": str(company_id),
                "worker_id": str(worker_id),
                "actor": {
                    "actor_id": "orchestrator-api",
                    "purpose": "approved live certification hiring",
                },
                "idempotency_key": f"mailbox:{company_id}:{worker_id}",
            }
            first = await _post(
                client, operator, "/v1/worker-identities/provision", provision_body
            )
            second = await _post(
                client, operator, "/v1/worker-identities/provision", provision_body
            )
            assert first.status_code == second.status_code == 200
            assert first.json()["id"] == second.json()["id"]
            identities[worker_id] = first.json()

        run_id = uuid4().hex
        subjects = {
            worker_a: f"AIAT live certification A {run_id}",
            worker_b: f"AIAT live certification B {run_id}",
        }
        codes = {worker_a: "481516", worker_b: "234215"}
        for worker_id in (worker_a, worker_b):
            await anyio.to_thread.run_sync(
                lambda current_worker=worker_id: _send_inbound(
                    hostname=mail_host,
                    port=mail_port,
                    envelope_from=envelope_from,
                    recipient=identities[current_worker]["address"],
                    subject=subjects[current_worker],
                    code=codes[current_worker],
                )
            )

        message_ids = {
            worker_id: await _find_message(
                client,
                worker_signers[worker_id],
                worker_id=worker_id,
                subject=subjects[worker_id],
            )
            for worker_id in (worker_a, worker_b)
        }

        for worker_id in (worker_a, worker_b):
            if identities[worker_id]["state"] == "IDENTITY_VERIFYING":
                verified = await _post(
                    client,
                    operator,
                    f"/v1/worker-identities/{worker_id}/verify",
                    {
                        "actor": {
                            "actor_id": "orchestrator-api",
                            "purpose": "live persisted-delivery verification",
                        },
                        "provider_message_id": message_ids[worker_id],
                    },
                )
                assert verified.status_code == 200, verified.text
                assert verified.json()["state"] == "IDENTITY_ACTIVE"

        extracted = await _post(
            client,
            worker_signers[worker_a],
            "/v1/mail/extract-code",
            {
                "worker_id": str(worker_a),
                "actor": {
                    "actor_id": str(worker_a),
                    "purpose": "live code extraction",
                },
                "message_id": message_ids[worker_a],
            },
        )
        assert extracted.status_code == 200, extracted.text
        assert extracted.json()["code"] == codes[worker_a]

        cross_worker = await _post(
            client,
            worker_signers[worker_a],
            "/v1/mail/read",
            {
                "worker_id": str(worker_b),
                "actor": {
                    "actor_id": str(worker_a),
                    "purpose": "negative cross-worker certification",
                },
                "message_id": message_ids[worker_b],
            },
        )
        assert cross_worker.status_code == 403

        forged_actor = await _post(
            client,
            worker_signers[worker_a],
            "/v1/mail/read",
            {
                "worker_id": str(worker_b),
                "actor": {
                    "actor_id": str(worker_b),
                    "purpose": "negative forged-actor certification",
                },
                "message_id": message_ids[worker_b],
            },
        )
        assert forged_actor.status_code == 403

        assert await anyio.to_thread.run_sync(
            lambda: _unknown_recipient_is_rejected(
                hostname=mail_host,
                port=mail_port,
                envelope_from=envelope_from,
                domain=identities[worker_a]["address"].split("@", 1)[1],
            )
        )

        outbound = await _post(
            client,
            worker_signers[worker_a],
            "/v1/outbound/request",
            {
                "worker_id": str(worker_a),
                "actor": {
                    "actor_id": str(worker_a),
                    "purpose": "approved live Resend certification",
                },
                "idempotency_key": f"live-outbound:{worker_a}:{run_id}",
                "recipients": [outbound_recipient],
                "subject": f"AIAT Resend certification {run_id}",
                "body": f"Reply with token {run_id}",
                "recipient_class": "approved_external",
            },
        )
        assert outbound.status_code == 200, outbound.text
        approval_id = outbound.json()["approval"]["id"]
        decision = await _post(
            client,
            operator,
            f"/v1/approvals/{approval_id}/decision",
            {
                "actor": {
                    "actor_id": "operator",
                    "purpose": "human live outbound approval",
                },
                "approved": True,
                "reason": "approved staging certification",
            },
        )
        assert decision.status_code == 200, decision.text
        submitted = await _post(
            client,
            worker_signers[worker_a],
            "/v1/outbound/send-approved",
            {
                "worker_id": str(worker_a),
                "actor": {
                    "actor_id": str(worker_a),
                    "purpose": "approved live Resend certification",
                },
                "outbound_request_id": outbound.json()["request"]["id"],
                "idempotency_key": f"live-submit:{worker_a}:{run_id}",
            },
        )
        assert submitted.status_code == 200, submitted.text
        assert submitted.json()["state"] == "SUBMITTED"
        assert submitted.json().get("provider_message_id")

        reply_id = await _find_message(
            client,
            worker_signers[worker_a],
            worker_id=worker_a,
            subject=f"Re: AIAT Resend certification {run_id}",
            timeout_seconds=int(
                os.getenv("LIVE_IDENTITY_REPLY_TIMEOUT_SECONDS", "600")
            ),
        )
        assert reply_id

        suspended = await _post(
            client,
            operator,
            f"/v1/worker-identities/{worker_b}/suspend",
            {
                "actor": {
                    "actor_id": "orchestrator-api",
                    "purpose": "explicit live suspension certification",
                }
            },
        )
        assert suspended.status_code == 200, suspended.text
        denied_after_suspend = await _post(
            client,
            worker_signers[worker_b],
            "/v1/mail/list",
            {
                "worker_id": str(worker_b),
                "actor": {
                    "actor_id": str(worker_b),
                    "purpose": "negative post-suspension certification",
                },
                "limit": 1,
            },
        )
        assert denied_after_suspend.status_code == 403
