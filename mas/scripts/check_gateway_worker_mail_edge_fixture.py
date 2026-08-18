"""Certify the local gateway-worker to mail-edge evidence composition.

The fixture runs the real ``GatewayWorkerAdapter`` and
``WorkerRunController`` with a bounded in-process gateway double, projects
scalar model/worker/integration trace sources, and evaluates the existing
payload-free worker/mail-edge join.  It proves that the selected trace and
worker identity, exact provider/model usage, verified webhook, and bounce
signals compose without exposing payloads or making network/provider calls.

This is local composition evidence only.  It does not claim a live worker,
external provider callback, durable provider read-back, outage recovery,
sandbox execution, or host certification.  Licence/restriction metadata is
not an operational predicate.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

MAS_ROOT = Path(__file__).resolve().parents[1]
CORE_ROOT = MAS_ROOT / "packages" / "mas-core"
if str(CORE_ROOT) not in sys.path:
    sys.path.insert(0, str(CORE_ROOT))

from mas_core.llm_gateway.models import ChatMessage, ChatResponse, UsageStats  # noqa: E402
from mas_core.observability.mail_edge import (  # noqa: E402
    build_mail_edge_observation,
    normalize_provider_webhook,
)
from mas_core.observability.trace_evidence import build_trace_evidence  # noqa: E402
from mas_core.observability.worker_trace_coverage import (  # noqa: E402
    evaluate_worker_mail_edge_coverage,
)
from mas_core.worker_contract.controller import WorkerRunController  # noqa: E402
from mas_core.worker_contract.models import ModelProfileReference, WorkerRunRequest  # noqa: E402
from mas_core.worker_registry.runtime_adapters import GatewayWorkerAdapter  # noqa: E402

SCHEMA = "aiat.gateway-worker-mail-edge-fixture.v1"
TRACE_ID = "gateway-worker-mail-edge-fixture-001"
SPAN_ID = "gateway-worker-mail-span-001"
WORKER_ID = "gateway-mail-worker-fixture"
PROVIDER_ID = "fixture-provider"
MODEL_ID = "fixture/model-v1"
TIMESTAMP = "2026-08-18T12:00:00Z"


class _FixtureGateway:
    """Bounded in-process gateway double used only by this certificate."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def chat_completion(self, **kwargs: Any) -> ChatResponse:
        self.calls.append(dict(kwargs))
        return ChatResponse(
            model=str(kwargs["model"]),
            message=ChatMessage(role="assistant", content="fixture answer"),
            usage=UsageStats(prompt_tokens=6, completion_tokens=4, total_tokens=10),
        )


def _trace_rows(result: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    usage = getattr(result, "usage", None)
    return (
        [
            {
                "id": "gateway-worker-mail-usage-001",
                "run_id": str(getattr(result, "run_id", "gateway-worker-mail-run-001")),
                "provider_id": getattr(usage, "provider", PROVIDER_ID),
                "exact_model_id": getattr(usage, "exact_model_id", MODEL_ID),
                "prompt_tokens": getattr(usage, "prompt_tokens", 0),
                "completion_tokens": getattr(usage, "completion_tokens", 0),
                "cost_usd": 0.0,
                "trace_id": TRACE_ID,
                "created_at": TIMESTAMP,
            }
        ],
        [
            {
                "id": "gateway-worker-mail-artifact-001",
                "run_id": str(getattr(result, "run_id", "gateway-worker-mail-run-001")),
                "artifact_id": 1,
                "kind": "report",
                "sha256": "a" * 64,
                "size_bytes": 64,
                "trace_id": TRACE_ID,
                "created_at": TIMESTAMP,
            }
        ],
        [
            {
                "id": "gateway-worker-mail-integration-001",
                "connection_id": "fixture-mail-connection",
                "evidence_type": "mail.provider_webhook.delivered",
                "trace_id": TRACE_ID,
                "created_at": TIMESTAMP,
            }
        ],
        [
            {
                "id": "gateway-worker-mail-model-span",
                "source_kind": "model",
                "operation": "llm_gateway.chat",
                "service": "llm_gateway",
                "status": "success",
                "started_at": TIMESTAMP,
                "trace_id": TRACE_ID,
            },
            {
                "id": "gateway-worker-mail-worker-span",
                "source_kind": "worker",
                "operation": "worker.execute",
                "service": "worker_run_controller",
                "status": "success",
                "started_at": TIMESTAMP,
                "trace_id": TRACE_ID,
            },
            {
                "id": "gateway-worker-mail-integration-span",
                "source_kind": "integration",
                "operation": "mail.provider_webhook.delivered",
                "service": "identity_service",
                "status": "success",
                "started_at": TIMESTAMP,
                "trace_id": TRACE_ID,
            },
        ],
    )


def _mail_observations() -> list[Any]:
    delivery = build_mail_edge_observation(
        provider="resend",
        source="delivery_attempt",
        event_id="gateway-worker-mail-attempt-001",
        event_type="queued",
        worker_id=WORKER_ID,
        outbound_request_id="gateway-worker-mail-request-001",
        provider_message_ref="gateway-worker-mail-message-001",
        trace_id=TRACE_ID,
        span_id="gateway-worker-mail-span-002",
        occurred_at=TIMESTAMP,
        metadata={"attempt_number": 1},
    )
    delivered = normalize_provider_webhook(
        "resend",
        {
            "id": "gateway-worker-mail-event-delivered",
            "type": "email.delivered",
            "created_at": TIMESTAMP,
            "data": {"email_id": "gateway-worker-mail-message-001"},
        },
        signature_verified=True,
        worker_id=WORKER_ID,
        trace_id=TRACE_ID,
        span_id="gateway-worker-mail-span-003",
    )
    bounced = normalize_provider_webhook(
        "resend",
        {
            "id": "gateway-worker-mail-event-bounced",
            "type": "email.bounced",
            "created_at": TIMESTAMP,
            "data": {"email_id": "gateway-worker-mail-message-002", "reason_code": "fixture"},
        },
        signature_verified=True,
        worker_id=WORKER_ID,
        trace_id=TRACE_ID,
        span_id="gateway-worker-mail-span-004",
    )
    return [delivery, delivered, bounced]


async def _fixture() -> dict[str, Any]:
    gateway = _FixtureGateway()
    adapter = GatewayWorkerAdapter(
        worker_id=WORKER_ID,
        provider_id=PROVIDER_ID,
        gateway_client=gateway,
    )
    request = WorkerRunRequest(
        idempotency_key="gateway-worker-mail-edge-fixture-v1",
        worker_id=WORKER_ID,
        task_type="gateway-worker-mail-edge-fixture",
        task_input={"prompt": "return a bounded fixture answer", "max_tokens": 32},
        trace_id=TRACE_ID,
        span_id=SPAN_ID,
        resolved_model_profile=ModelProfileReference(
            profile_id="fixture-profile-v1",
            version="v1",
            exact_model_id=MODEL_ID,
        ),
    )
    try:
        outcome = await WorkerRunController().execute(request, adapter)
    finally:
        await adapter.close()

    result = outcome.result
    usage_rows, artifact_rows, integration_rows, native_rows = _trace_rows(result)
    evidence = build_trace_evidence(
        trace_id=TRACE_ID,
        worker_usage_rows=usage_rows,
        artifact_rows=artifact_rows,
        integration_evidence_rows=integration_rows,
        native_span_rows=native_rows,
        generated_at=TIMESTAMP,
    )
    combined = evaluate_worker_mail_edge_coverage(
        evidence,
        _mail_observations(),
        trace_id=TRACE_ID,
        worker_id=WORKER_ID,
        require_integration=True,
        require_mail_edge=True,
    )
    usage = getattr(result, "usage", None)
    report: dict[str, Any] = {
        "schema_version": SCHEMA,
        "mode": "fixture",
        "status": "pass" if outcome.state == "SUCCEEDED" and combined["status"] == "pass" else "fail",
        "licence_metadata_is_gate": False,
        "controller_terminal_state": outcome.state,
        "gateway_call_count": len(gateway.calls),
        "provider_id": PROVIDER_ID,
        "exact_model_id": MODEL_ID,
        "trace_id": TRACE_ID,
        "worker_id": WORKER_ID,
        "worker_trace_status": combined["worker_trace"]["status"],
        "mail_edge_status": combined["mail_edge"]["status"],
        "combined_status": combined["status"],
        "required_sources": combined["worker_trace"]["required_sources"],
        "mail_edge_event_counts": combined["mail_edge"]["event_counts"],
        "usage_attribution_match": bool(
            usage is not None
            and usage.provider == PROVIDER_ID
            and usage.exact_model_id == MODEL_ID
        ),
        "verified_webhook_and_bounce": bool(
            combined["mail_edge"]["signed_webhook_count"] >= 1
            and combined["mail_edge"]["bounce_or_failure_count"] >= 1
        ),
        "network_access_performed": False,
        "external_provider_call_performed": False,
        "mutation_performed": False,
        "sandbox_execution_performed": False,
        "payload_free_report": True,
        "scope": "real GatewayWorkerAdapter/WorkerRunController plus scalar trace and mail-edge evaluators; local fixture only",
    }
    serialized = json.dumps(report, sort_keys=True)
    if any(marker in serialized for marker in ("private@example.net", "provider body", "secret")):
        report["status"] = "fail"
        report["payload_free_report"] = False
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args(argv)
    report = asyncio.run(_fixture())
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        print(f"gateway worker/mail-edge fixture: {report['status']} — {report['scope']}")
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
