from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

MAS_ROOT = Path(__file__).resolve().parents[1]
if str(MAS_ROOT) not in sys.path:
    sys.path.insert(0, str(MAS_ROOT))

import certify_cloudflare_mail_edge_live as certificate  # noqa: E402
from identity_service.providers.inbound.cloudflare import (  # noqa: E402
    CloudflareEdgeFixture,
    CloudflareInboundAdapter,
)


def _args(arguments: list[str]):
    return certificate._parser().parse_args(arguments)


@pytest.mark.anyio
async def test_live_certificate_commands_use_real_adapter_and_keep_report_payload_free() -> None:
    edge = CloudflareEdgeFixture(auth_secret="fixture-cloudflare-secret-012345")
    async with httpx.AsyncClient(transport=httpx.MockTransport(edge.handle)) as client:
        adapter = CloudflareInboundAdapter(
            edge_url="https://mail-edge.example",
            auth_secret=edge.auth_secret,
            client=client,
        )
        recipient = "w-live-smoke@agents.aiat.ca"
        identity_id = "live-smoke-identity"
        worker_id = "live-smoke-worker"
        registered = await certificate.execute_command(
            _args(
                [
                    "register-smoke-recipient",
                    "--recipient",
                    recipient,
                    "--identity-id",
                    identity_id,
                    "--worker-id",
                    worker_id,
                ]
            ),
            adapter,
        )
        assert registered["status"] == "PASS"
        provider_reference = registered["provider_reference"]

        raw = (
            b"From: external@example.invalid\r\n"
            b"To: forged-other@agents.aiat.ca\r\n"
            b"Message-ID: <live-certificate@example.invalid>\r\n"
            b"Subject: AIAT live inbound smoke test\r\n\r\n"
            b"verification-code=481516 must never appear in evidence\r\n"
        )
        delivery = edge.receive_message(recipient, raw)
        assert delivery["accepted"] is True

        inspected = await certificate.execute_command(
            _args(["inspect-events", "--recipient", recipient]),
            adapter,
        )
        assert inspected["status"] == "PASS"
        assert inspected["event_count"] == 1
        assert inspected["events"][0]["event_type"] == "inbound.message.received"

        fetched = await certificate.execute_command(
            _args(
                [
                    "fetch-and-validate-message",
                    "--recipient",
                    recipient,
                    "--identity-id",
                    identity_id,
                    "--worker-id",
                    worker_id,
                    "--message-id",
                    delivery["message_id"],
                    "--expected-subject",
                    "AIAT live inbound smoke test",
                    "--include-subject",
                ]
            ),
            adapter,
        )
        assert fetched["status"] == "PASS"
        assert fetched["checks"]["subject_matches"] is True
        assert fetched["message"]["subject"] == "AIAT live inbound smoke test"

        finalized = await certificate.execute_command(
            _args(
                [
                    "finalize-certification",
                    "--recipient",
                    recipient,
                    "--identity-id",
                    identity_id,
                    "--worker-id",
                    worker_id,
                    "--provider-reference",
                    provider_reference,
                ]
            ),
            adapter,
        )
        assert finalized["status"] == "PASS"
        assert finalized["lifecycle_state"] == "RETIRED"
        assert edge.receive_message(recipient, raw)["reason"] == "recipient_inactive"

        serialized = json.dumps(
            {
                "registered": registered,
                "inspected": inspected,
                "fetched": fetched,
                "finalized": finalized,
            },
            sort_keys=True,
        )
        assert "external@example.invalid" not in serialized
        assert "verification-code=481516" not in serialized
        assert "must never appear in evidence" not in serialized


def test_live_certificate_fails_closed_without_secret_environment(monkeypatch, capsys) -> None:
    monkeypatch.delenv("CLOUDFLARE_MAIL_EDGE_URL", raising=False)
    monkeypatch.delenv("CLOUDFLARE_MAIL_EDGE_AUTH_SECRET", raising=False)

    assert certificate.main(["--json", "health"]) == 1
    output = capsys.readouterr().out
    report = json.loads(output)
    assert report["status"] == "FAIL"
    assert report["error_code"] == "CERTIFICATION_INPUT_INVALID"
    assert "CLOUDFLARE_MAIL_EDGE_AUTH_SECRET" not in output
