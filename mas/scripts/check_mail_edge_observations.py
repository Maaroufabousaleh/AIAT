"""Check bounded identity/provider mail-edge and bounce observations.

Fixture mode exercises the shared normalizer with delivery, verified provider
webhook, and permanent-bounce observations.  Live mode only reads one
operator-selected trace from the orchestrator.  It requires an explicitly
selected representative worker and never dispatches a worker, sends mail, or
changes provider state.  A live pass is reported only when the deployment
returns signed/provider webhook and bounce coverage; ordinary delivery-attempt
rows remain an explicit ``attention`` result.  Licence metadata is never a
predicate.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx

MAS_ROOT = Path(__file__).resolve().parents[1]
if str(MAS_ROOT / "packages" / "mas-core") not in sys.path:
    sys.path.insert(0, str(MAS_ROOT / "packages" / "mas-core"))

from mas_core.observability.mail_edge import (  # noqa: E402
    MAIL_EDGE_COVERAGE_SCHEMA,
    MAIL_EDGE_OBSERVATION_SCHEMA,
    build_mail_edge_observation,
    evaluate_mail_edge_coverage,
    normalize_provider_webhook,
)

CHECK_SCHEMA = "aiat.mail-edge-observation-check.v1"
TRACE_SCHEMA = "aiat.trace-evidence.v1"
FIXTURE_WORKER_ID = "00000000-0000-4000-a000-000000000911"
FIXTURE_TRACE_ID = "fixture-mail-edge-trace-001"
_EVENT_TYPES = frozenset(
    {"queued", "sent", "delivered", "deferred", "bounced", "complained", "failed", "unknown"}
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument("--live", action="store_true", help="read one configured orchestrator trace")
    parser.add_argument(
        "--url",
        default=os.getenv("AIAT_ORCHESTRATOR_URL", os.getenv("ORCHESTRATOR_API_URL", "")),
        help="orchestrator base URL",
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("AIAT_OPERATOR_API_KEY", os.getenv("AIAT_API_KEY", "")),
        help="operator API key; never included in the report",
    )
    parser.add_argument(
        "--worker-id",
        default=os.getenv("AIAT_LIVE_WORKER_ID", ""),
        help="operator-selected representative model-backed worker UUID",
    )
    parser.add_argument(
        "--trace-id",
        default=os.getenv("AIAT_LIVE_WORKER_TRACE_ID", ""),
        help="trace produced by the selected worker run",
    )
    parser.add_argument("--limit", type=int, default=300)
    parser.add_argument("--timeout", type=float, default=15.0)
    return parser


def _blocked(reason: str, *, url_configured: bool = False) -> dict[str, Any]:
    return {
        "schema_version": CHECK_SCHEMA,
        "coverage_schema": MAIL_EDGE_COVERAGE_SCHEMA,
        "mode": "live",
        "status": "blocked",
        "licence_metadata_is_gate": False,
        "reason": reason,
        "url_configured": url_configured,
        "worker_selection": "required",
        "scope": "read-only selected-worker trace inspection; no dispatch or provider mutation",
    }


def _safe_observation_summary(value: Mapping[str, Any]) -> dict[str, Any]:
    """Return only scalar fields appropriate for a checker report."""

    return {
        "provider": str(value.get("provider") or "unknown"),
        "source": str(value.get("source") or "unknown"),
        "event_type": str(value.get("event_type") or "unknown"),
        "outcome": str(value.get("outcome") or "unknown"),
        "failure_class": value.get("failure_class"),
        "trace_id": value.get("trace_id"),
        "span_id": value.get("span_id"),
        "signature_verified": bool(value.get("signature_verified")),
        "metadata_keys": sorted(str(key) for key in (value.get("metadata") or {}) if isinstance(key, str)),
    }


def _fixture() -> dict[str, Any]:
    delivery = build_mail_edge_observation(
        provider="resend",
        source="delivery_attempt",
        event_id="attempt-fixture-001",
        event_type="queued",
        worker_id=FIXTURE_WORKER_ID,
        outbound_request_id="00000000-0000-4000-a000-000000000912",
        provider_message_ref="provider-message-fixture-001",
        trace_id=FIXTURE_TRACE_ID,
        span_id="fixture-mail-span-001",
        metadata={"attempt_number": 1, "recipient": "must be dropped"},
    )
    delivered = normalize_provider_webhook(
        "resend",
        {
            "id": "provider-event-fixture-delivered",
            "type": "email.delivered",
            "created_at": "2026-08-17T12:00:00Z",
            "data": {
                "email_id": "provider-message-fixture-001",
                "status": "delivered",
                "recipient": "private@example.net",
                "subject": "must not enter report",
            },
        },
        signature_verified=True,
        worker_id=FIXTURE_WORKER_ID,
        outbound_request_id="00000000-0000-4000-a000-000000000912",
        trace_id=FIXTURE_TRACE_ID,
        span_id="fixture-mail-span-002",
    )
    bounced = normalize_provider_webhook(
        "resend",
        {
            "id": "provider-event-fixture-bounced",
            "type": "email.bounced",
            "data": {
                "email_id": "provider-message-fixture-002",
                "status": "permanent",
                "reason_code": "mailbox_not_found",
                "body": "provider body must not enter report",
            },
        },
        signature_verified=True,
        worker_id=FIXTURE_WORKER_ID,
        trace_id=FIXTURE_TRACE_ID,
        span_id="fixture-mail-span-003",
    )
    observations = [delivery, delivered, bounced]
    coverage = evaluate_mail_edge_coverage(
        observations,
        trace_id=FIXTURE_TRACE_ID,
        worker_id=FIXTURE_WORKER_ID,
    )
    serialized = json.dumps([item.model_dump(mode="json") for item in observations], sort_keys=True)
    if any(secret in serialized for secret in ("private@example.net", "must not enter report", "provider body must not enter report")):
        coverage = {**coverage, "status": "fail", "missing": ["payload_redaction"]}
    return {
        "schema_version": CHECK_SCHEMA,
        "coverage_schema": coverage["schema_version"],
        "observation_schema": MAIL_EDGE_OBSERVATION_SCHEMA,
        "mode": "fixture",
        "status": coverage["status"],
        "licence_metadata_is_gate": False,
        "worker_id": FIXTURE_WORKER_ID,
        "trace_id": FIXTURE_TRACE_ID,
        "coverage": coverage,
        "observations": [_safe_observation_summary(item.model_dump(mode="json")) for item in observations],
        "network_access_performed": False,
        "mutation_performed": False,
        "payload_free_report": True,
        "scope": "shared normalizer fixture; no identity database or provider endpoint",
    }


def _trace_mail_rows(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("items")
    result: list[dict[str, Any]] = []
    if not isinstance(rows, list):
        return result
    for row in rows:
        if not isinstance(row, Mapping) or row.get("source") != "native_spans":
            continue
        operation = str(row.get("operation") or "").lower()
        service = str(row.get("service") or "").lower()
        if "mail" in operation or "mail" in service:
            result.append(dict(row))
    return result


def _trace_mail_observation(row: Mapping[str, Any], *, trace_id: str) -> Any:
    """Reduce one native mail span to the shared payload-free observation model."""

    operation = str(row.get("operation") or "").strip().lower()
    service = str(row.get("service") or "").strip().lower()
    provider_webhook = ".provider_webhook." in operation
    event_type = operation.rsplit(".provider_webhook.", 1)[-1] if provider_webhook else ""
    if event_type not in _EVENT_TYPES:
        event_type = "delivered" if str(row.get("status") or "").lower() == "success" else "failed"
    provider = "resend" if "resend" in service or "resend" in operation else "identity_service"
    return build_mail_edge_observation(
        provider=provider,
        source="provider_webhook" if provider_webhook else "delivery_attempt",
        event_id=str(row.get("id") or "unknown"),
        event_type=event_type,
        trace_id=trace_id,
        span_id=str(row.get("span_id") or "") or None,
        signature_verified=provider_webhook,
    )


async def _live(args: argparse.Namespace) -> dict[str, Any]:
    url = str(args.url or "").strip().rstrip("/")
    api_key = str(args.api_key or "").strip()
    worker_id = str(args.worker_id or "").strip()
    trace_id = str(args.trace_id or "").strip()
    if not url or not api_key:
        return _blocked("missing orchestrator URL or operator API key")
    if not worker_id:
        return _blocked("an explicitly selected representative worker is required", url_configured=True)
    if not trace_id:
        return _blocked("a trace ID produced by the selected worker is required", url_configured=True)
    try:
        async with httpx.AsyncClient(timeout=args.timeout, follow_redirects=False) as client:
            response = await client.get(
                f"{url}/observability/traces/{trace_id}",
                params={"limit": max(1, min(int(args.limit), 300))},
                headers={"X-API-Key": api_key},
            )
    except httpx.HTTPError as exc:
        return _blocked(f"orchestrator trace read unavailable: {type(exc).__name__}", url_configured=True)
    if response.status_code in {401, 403, 404, 409, 429, 503}:
        return _blocked(f"orchestrator trace read returned HTTP {response.status_code}", url_configured=True)
    if response.status_code >= 400:
        return {
            **_blocked(f"orchestrator trace read failed with HTTP {response.status_code}", url_configured=True),
            "status": "fail",
        }
    try:
        payload = response.json()
    except ValueError:
        return {**_blocked("orchestrator returned non-JSON trace evidence", url_configured=True), "status": "fail"}
    if not isinstance(payload, Mapping) or payload.get("schema_version") != TRACE_SCHEMA:
        return {**_blocked("orchestrator returned an invalid trace evidence model", url_configured=True), "status": "fail"}
    mail_rows = _trace_mail_rows(payload)
    # Future identity projections may expose normalized coverage alongside the
    # generic span rows.  Keep this parser additive and payload-free.
    projected = payload.get("mail_edge")
    if isinstance(projected, Mapping):
        coverage = dict(projected)
        status = str(coverage.get("status") or "attention")
    else:
        coverage = evaluate_mail_edge_coverage(
            [_trace_mail_observation(row, trace_id=trace_id) for row in mail_rows],
            trace_id=trace_id,
        )
        status = "attention" if mail_rows else "blocked"
        coverage = {
            **coverage,
            "status": status,
            "missing": sorted(set(coverage.get("missing", [])) | {"verified_provider_webhook", "bounce_or_failure_event"}),
        }
    return {
        "schema_version": CHECK_SCHEMA,
        "coverage_schema": MAIL_EDGE_COVERAGE_SCHEMA,
        "observation_schema": MAIL_EDGE_OBSERVATION_SCHEMA,
        "mode": "live",
        "status": status,
        "licence_metadata_is_gate": False,
        "worker_id_selected": worker_id,
        "trace_id_used": trace_id,
        "trace_status": payload.get("status"),
        "mail_span_count": len(mail_rows),
        "coverage": coverage,
        "network_access_performed": True,
        "mutation_performed": False,
        "payload_free_report": True,
        "scope": "read-only selected-worker trace inspection; provider webhook/bounce coverage is reported only when projected",
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = asyncio.run(_live(args)) if args.live else _fixture()
    print(json.dumps(report, sort_keys=True, indent=2) if args.json else f"mail-edge: {report['status']}")
    return 0 if report["status"] == "pass" else (2 if report["status"] == "blocked" else 1)


if __name__ == "__main__":
    raise SystemExit(main())
