from __future__ import annotations

from mas_core.observability.mail_edge import (
    MAIL_EDGE_COVERAGE_SCHEMA,
    MAIL_EDGE_OBSERVATION_SCHEMA,
    evaluate_mail_edge_coverage,
    normalize_provider_webhook,
)


def _fixture_rows() -> list[dict[str, object]]:
    trace_id = "fixture-mail-trace-001"
    worker_id = "00000000-0000-4000-a000-000000000901"
    delivered = normalize_provider_webhook(
        "resend",
        {
            "id": "evt-delivered-001",
            "type": "email.delivered",
            "created_at": "2026-08-17T12:00:00Z",
            "data": {
                "email_id": "provider-message-001",
                "status": "delivered",
                "to": ["private@example.net"],
                "subject": "must not escape",
            },
        },
        signature_verified=True,
        worker_id=worker_id,
        outbound_request_id="00000000-0000-4000-a000-000000000902",
        trace_id=trace_id,
    )
    bounced = normalize_provider_webhook(
        "resend",
        {
            "id": "evt-bounced-001",
            "type": "email.bounced",
            "data": {
                "email_id": "provider-message-002",
                "status": "permanent",
                "reason_code": "mailbox_not_found",
                "body": "private provider payload",
            },
        },
        signature_verified=True,
        worker_id=worker_id,
        trace_id=trace_id,
    )
    return [
        {
            "schema_version": MAIL_EDGE_OBSERVATION_SCHEMA,
            "id": "00000000-0000-4000-a000-000000000903",
            "provider": "resend",
            "source": "delivery_attempt",
            "event_id": "attempt-001",
            "event_type": "queued",
            "outcome": "success",
            "failure_class": None,
            "worker_id": worker_id,
            "outbound_request_id": "00000000-0000-4000-a000-000000000902",
            "provider_message_ref": "provider-message-001",
            "trace_id": trace_id,
            "span_id": "fixture-mail-span-001",
            "occurred_at": "2026-08-17T11:59:00Z",
            "signature_verified": False,
            "metadata": {"attempt_number": 1},
        },
        delivered.model_dump(mode="json"),
        bounced.model_dump(mode="json"),
    ]


def test_provider_webhook_normalization_is_bounded_and_payload_free() -> None:
    rows = _fixture_rows()
    serialized = str(rows)
    assert "private@example.net" not in serialized
    assert "must not escape" not in serialized
    assert "private provider payload" not in serialized
    delivered = rows[1]
    assert delivered["event_type"] == "delivered"
    assert delivered["outcome"] == "success"
    assert delivered["metadata"]["provider_status"] == "delivered"  # type: ignore[index]
    assert delivered["metadata"].get("to") is None  # type: ignore[union-attr]


def test_mail_edge_coverage_requires_verified_webhook_and_failure_signal() -> None:
    report = evaluate_mail_edge_coverage(
        _fixture_rows(),
        trace_id="fixture-mail-trace-001",
        worker_id="00000000-0000-4000-a000-000000000901",
    )
    assert report["schema_version"] == MAIL_EDGE_COVERAGE_SCHEMA
    assert report["status"] == "pass"
    assert report["source_counts"] == {
        "delivery_attempt": 1,
        "provider_webhook": 2,
        "provider_poll": 0,
    }
    assert report["bounce_or_failure_count"] == 1
    assert report["licence_metadata_is_gate"] is False


def test_conflicting_event_id_fails_closed_to_attention() -> None:
    rows = _fixture_rows()
    conflicting = dict(rows[1])
    conflicting["event_type"] = "bounced"
    conflicting["outcome"] = "failure"
    conflicting["failure_class"] = "permanent"
    report = evaluate_mail_edge_coverage([rows[1], conflicting])
    assert report["status"] == "attention"
    assert report["conflict_event_ids"] == ["evt-delivered-001"]
    assert "conflicting_event_id" in report["missing"]


def test_unsigned_webhook_does_not_count_as_provider_evidence() -> None:
    row = _fixture_rows()[1]
    row["signature_verified"] = False
    report = evaluate_mail_edge_coverage([row])
    assert report["status"] == "attention"
    assert "verified_provider_webhook" in report["missing"]
