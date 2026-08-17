from __future__ import annotations

from check_mail_edge_observations import _trace_mail_observation


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
