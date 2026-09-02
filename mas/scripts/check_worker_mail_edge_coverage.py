"""Certify the bounded worker-trace/mail-edge evidence join.

The fixture composes the real worker source evaluator with the real
payload-free mail-edge normalizer/evaluator.  It is deliberately not a live
worker or provider probe: no worker is selected, activated, or dispatched and
no provider endpoint is contacted.  Licence or restriction metadata is never
an execution predicate.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from mas_core.observability.mail_edge import normalize_provider_webhook
from mas_core.observability.trace_evidence import build_trace_evidence
from mas_core.observability.worker_trace_coverage import (
    WORKER_MAIL_EDGE_COVERAGE_SCHEMA,
    evaluate_worker_mail_edge_coverage,
)

CHECK_SCHEMA = "aiat.worker-mail-edge-coverage-check.v1"
TRACE_ID = "fixture-worker-mail-edge-trace-001"
WORKER_ID = "00000000-0000-4000-a000-000000000921"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument(
        "--require-integration",
        action="store_true",
        help="require native and durable integration sources in the worker trace",
    )
    return parser


def _fixture(*, require_integration: bool) -> dict[str, Any]:
    evidence = build_trace_evidence(
        trace_id=TRACE_ID,
        worker_usage_rows=[
            {
                "id": "fixture-worker-mail-usage-001",
                "run_id": "fixture-worker-mail-run-001",
                "provider_id": "omniroute",
                "exact_model_id": "fixture-model",
                "prompt_tokens": 8,
                "completion_tokens": 12,
                "cost_usd": 0.01,
                "created_at": "2026-08-17T12:00:01Z",
            }
        ],
        artifact_rows=[
            {
                "id": "fixture-worker-mail-artifact-001",
                "run_id": "fixture-worker-mail-run-001",
                "artifact_id": 1,
                "size_bytes": 64,
                "sha256": "a" * 64,
                "created_at": "2026-08-17T12:00:02Z",
            }
        ],
        integration_evidence_rows=[
            {
                "id": "fixture-worker-mail-integration-001",
                "connection_id": "fixture-connection",
                "evidence_type": "issue.updated",
                "created_at": "2026-08-17T12:00:03Z",
            }
        ],
        native_span_rows=[
            {
                "id": "fixture-worker-mail-model-span",
                "source_kind": "model",
                "operation": "omniroute.chat",
                "service": "llm_gateway",
                "status": "success",
                "started_at": "2026-08-17T12:00:01Z",
            },
            {
                "id": "fixture-worker-mail-worker-span",
                "source_kind": "worker",
                "operation": "worker.execute",
                "service": "team_runner",
                "status": "success",
                "started_at": "2026-08-17T12:00:01Z",
            },
            {
                "id": "fixture-worker-mail-integration-span",
                "source_kind": "integration",
                "operation": "pm.issue.updated",
                "service": "pm_gateway",
                "status": "success",
                "started_at": "2026-08-17T12:00:03Z",
            },
        ],
        generated_at="2026-08-17T12:00:04Z",
    )
    delivery = {
        "schema_version": "aiat.mail-edge-observation.v1",
        "id": "00000000-0000-4000-a000-000000000922",
        "provider": "resend",
        "source": "delivery_attempt",
        "event_id": "fixture-worker-mail-attempt-001",
        "event_type": "queued",
        "outcome": "success",
        "failure_class": None,
        "worker_id": WORKER_ID,
        "outbound_request_id": "00000000-0000-4000-a000-000000000923",
        "provider_message_ref": "fixture-worker-mail-message-001",
        "trace_id": TRACE_ID,
        "span_id": "fixture-worker-mail-span-001",
        "occurred_at": "2026-08-17T11:59:59Z",
        "signature_verified": False,
        "metadata": {"attempt_number": 1},
    }
    delivered = normalize_provider_webhook(
        "resend",
        {
            "id": "fixture-worker-mail-event-delivered",
            "type": "email.delivered",
            "created_at": "2026-08-17T12:00:05Z",
            "data": {"email_id": "fixture-worker-mail-message-001", "status": "delivered"},
        },
        signature_verified=True,
        worker_id=WORKER_ID,
        trace_id=TRACE_ID,
        span_id="fixture-worker-mail-span-002",
    )
    bounced = normalize_provider_webhook(
        "resend",
        {
            "id": "fixture-worker-mail-event-bounced",
            "type": "email.bounced",
            "data": {
                "email_id": "fixture-worker-mail-message-002",
                "status": "permanent",
                "reason_code": "mailbox_not_found",
            },
        },
        signature_verified=True,
        worker_id=WORKER_ID,
        trace_id=TRACE_ID,
        span_id="fixture-worker-mail-span-003",
    )
    combined = evaluate_worker_mail_edge_coverage(
        evidence,
        [delivery, delivered, bounced],
        trace_id=TRACE_ID,
        worker_id=WORKER_ID,
        require_integration=require_integration,
        require_mail_edge=True,
    )
    report: dict[str, Any] = {
        "schema_version": CHECK_SCHEMA,
        "coverage_schema": WORKER_MAIL_EDGE_COVERAGE_SCHEMA,
        "mode": "fixture",
        "status": combined["status"],
        "licence_metadata_is_gate": False,
        "trace_id": TRACE_ID,
        "worker_id": WORKER_ID,
        "require_integration": bool(require_integration),
        "worker_trace_status": combined["worker_trace"]["status"],
        "worker_trace_required_sources": combined["worker_trace"]["required_sources"],
        "mail_edge_status": combined["mail_edge"]["status"],
        "mail_edge_source_counts": combined["mail_edge"]["source_counts"],
        "mail_edge_event_counts": combined["mail_edge"]["event_counts"],
        "mail_edge_missing": combined["mail_edge"]["missing"],
        "missing_required_signals": combined["missing_required_signals"],
        "worker_trace_item_count": evidence.item_count,
        "network_access_performed": False,
        "mutation_performed": False,
        "payload_free_report": True,
        "scope": combined["scope"],
    }
    serialized = json.dumps(report, sort_keys=True)
    if any(secret in serialized for secret in ("private@example.net", "must not escape", "provider body")):
        report["status"] = "fail"
        report["payload_free_report"] = False
        report["missing_required_signals"] = sorted(
            set(report["missing_required_signals"]) | {"payload_redaction"}
        )
    return report


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = _fixture(require_integration=bool(args.require_integration))
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        print(f"worker/mail-edge coverage: {report['status']}")
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
