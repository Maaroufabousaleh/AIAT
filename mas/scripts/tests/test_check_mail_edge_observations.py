from __future__ import annotations

import base64
import hashlib
import json
from types import SimpleNamespace

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from check_mail_edge_observations import _identity_mail_readback, _trace_mail_observation


def test_trace_mail_projection_preserves_provider_event_and_signature_boundary() -> None:
    observation = _trace_mail_observation(
        {
            "id": "provider-event-1",
            "operation": "mail.provider_webhook.bounced",
            "service": "identity_mail_edge_provider_webhook",
            "status": "failed",
            "span_id": "span-mail-edge-001",
        },
        trace_id="trace-mail-edge-001",
    )

    assert observation.provider == "identity_service"
    assert observation.source == "provider_webhook"
    assert observation.event_type == "bounced"
    assert observation.signature_verified is True
    assert observation.trace_id == "trace-mail-edge-001"
    assert observation.span_id == "span-mail-edge-001"


def test_trace_mail_projection_keeps_delivery_attempts_unsigned() -> None:
    observation = _trace_mail_observation(
        {
            "id": "delivery-attempt-1",
            "operation": "mail.delivery_attempt",
            "service": "identity_outbound_delivery_attempts",
            "status": "success",
        },
        trace_id="trace-mail-edge-001",
    )

    assert observation.source == "delivery_attempt"
    assert observation.event_type == "delivered"
    assert observation.signature_verified is False


@pytest.mark.anyio
async def test_identity_readback_uses_signed_dashboard_and_keeps_only_selected_safe_rows() -> None:
    private = Ed25519PrivateKey.generate()
    observed: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        observed["path"] = request.url.path
        observed["body"] = json.loads(request.content)
        signature = base64.b64decode(request.headers["X-AIAT-Signature"])
        canonical = (
            "aiat.identity.v1\nPOST\n/v1/dashboard/mail-edge\n"
            f"{request.headers['X-AIAT-Timestamp']}\n{request.headers['X-AIAT-Nonce']}\n"
            f"{hashlib.sha256(request.content).hexdigest()}"
        ).encode()
        private.public_key().verify(signature, canonical)
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "edge-row-1",
                        "provider": "resend",
                        "source": "provider_webhook",
                        "event_id": "provider-event-1",
                        "event_type": "bounced",
                        "trace_id": "trace-mail-edge-001",
                        "span_id": "span-mail-edge-001",
                        "occurred_at": "2026-08-17T12:00:00Z",
                        "signature_verified": True,
                        "metadata": {"provider_reason_code": "550", "body": "drop-me"},
                    },
                    {
                        "id": "edge-row-other-trace",
                        "provider": "resend",
                        "source": "provider_webhook",
                        "event_id": "provider-event-other",
                        "event_type": "delivered",
                        "trace_id": "other-trace",
                        "signature_verified": True,
                    },
                ]
            },
        )

    args = SimpleNamespace(
        identity_url="http://identity",
        identity_client_id="operator-laptop",
        identity_private_key=base64.b64encode(private.private_bytes_raw()).decode(),
        timeout=2.0,
        limit=50,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await _identity_mail_readback(client=client, args=args, trace_id="trace-mail-edge-001")

    assert observed["path"] == "/v1/dashboard/mail-edge"
    assert observed["body"] == {"limit": 50}
    assert result["status"] == "read"
    assert result["row_count"] == 1
    observation = result["observations"][0]
    assert observation.event_type == "bounced"
    assert observation.metadata == {"provider_reason_code": "550"}
    assert "drop-me" not in str(result)


@pytest.mark.anyio
async def test_identity_readback_fails_closed_when_configuration_is_partial() -> None:
    args = SimpleNamespace(
        identity_url="http://identity",
        identity_client_id="operator-laptop",
        identity_private_key="",
        timeout=2.0,
        limit=50,
    )

    result = await _identity_mail_readback(args=args, trace_id="trace-mail-edge-001")

    assert result["status"] == "blocked"
    assert result["observations"] == []
