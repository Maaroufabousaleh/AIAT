"""Check the bounded AIAT trace-incident projection.

The default mode uses a deterministic trace-evidence fixture. ``--live`` reads
the operator-authenticated trace evidence endpoint and emits only incident
status, coverage, counts, notice codes, and stable finding references. An
unavailable endpoint is ``blocked`` (exit 2); an incident marked ``attention``
is an observed operational result, not a release or execution gate.
"""

from __future__ import annotations

import argparse
import json
import os
from typing import Any

import httpx

from mas_core.observability.trace_evidence import TraceEvidence, build_trace_evidence
from mas_core.observability.trace_incident import TRACE_INCIDENT_SCHEMA, build_trace_incident
from mas_core.observability.tracing import is_safe_trace_id

CHECK_SCHEMA = "aiat.trace-incident-check.v1"
TRACE_SCHEMA = "aiat.trace-evidence.v1"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument("--live", action="store_true", help="query the operator trace endpoint")
    parser.add_argument(
        "--url",
        default=os.getenv("AIAT_ORCHESTRATOR_URL", os.getenv("ORCHESTRATOR_API_URL", "")),
        help="orchestrator base URL (or AIAT_ORCHESTRATOR_URL/ORCHESTRATOR_API_URL)",
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("AIAT_OPERATOR_API_KEY", os.getenv("AIAT_API_KEY", "")),
        help="operator API key; never included in the report",
    )
    parser.add_argument("--trace-id", help="bounded trace/message ID to query in live mode")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--timeout", type=float, default=15.0)
    return parser


def _summary(*, mode: str, incident: Any, scope: str) -> dict[str, Any]:
    return {
        "schema_version": CHECK_SCHEMA,
        "incident_schema": TRACE_INCIDENT_SCHEMA,
        "mode": mode,
        "status": "pass" if mode == "fixture" else "observed",
        "incident_status": incident.status,
        "severity": incident.severity,
        "coverage_status": incident.coverage_status,
        "trace_id": incident.trace_id,
        "item_count": incident.item_count,
        "finding_count": incident.finding_count,
        "source_counts": dict(incident.source_counts),
        "affected_sources": list(incident.affected_sources),
        "finding_refs": [finding.id for finding in incident.findings],
        "notice_codes": list(incident.notice_codes),
        "scope": scope,
    }


def _fixture() -> dict[str, Any]:
    evidence = build_trace_evidence(
        trace_id="fixture-incident-001",
        api_rows=[
            {
                "id": "fixture-api-503",
                "method": "POST",
                "route": "/workers",
                "status_code": 503,
                "outcome": "error",
                "occurred_at": "2026-08-17T00:00:00+00:00",
            }
        ],
        transition_rows=[
            {
                "id": "fixture-transition-failed",
                "run_id": "fixture-run-001",
                "to_state": "FAILED",
                "created_at": "2026-08-17T00:00:01+00:00",
            }
        ],
        native_span_rows=[
            {
                "id": "fixture-native-failed",
                "source_kind": "worker",
                "operation": "worker.run",
                "service": "worker-runtime",
                "status": "failed",
                "started_at": "2026-08-17T00:00:02+00:00",
            }
        ],
        generated_at="2026-08-17T00:00:03+00:00",
    )
    incident = build_trace_incident(evidence)
    report = _summary(
        mode="fixture",
        incident=incident,
        scope="deterministic trace-evidence fixture; no database, worker, or provider state was changed",
    )
    report["fixture_expectation"] = {"incident_status": "attention", "severity": "critical"}
    return report


def _blocked(reason: str, *, url_configured: bool = False) -> dict[str, Any]:
    return {
        "schema_version": CHECK_SCHEMA,
        "incident_schema": TRACE_INCIDENT_SCHEMA,
        "mode": "live",
        "status": "blocked",
        "reason": reason,
        "url_configured": url_configured,
        "scope": "operator-authenticated trace-incident summary only",
    }


def _live(*, url: str, api_key: str, trace_id: str | None, limit: int, timeout: float) -> dict[str, Any]:
    if not url.strip():
        return _blocked("missing live configuration: orchestrator URL")
    if not trace_id or not is_safe_trace_id(trace_id):
        return _blocked("missing or invalid bounded trace ID", url_configured=True)
    headers = {"X-API-Key": api_key} if api_key.strip() else {}
    try:
        response = httpx.get(
            f"{url.rstrip('/')}/observability/traces/{trace_id}",
            headers=headers,
            params={"limit": max(1, min(limit, 300))},
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("schema_version") != TRACE_SCHEMA:
            return _blocked("orchestrator returned an invalid trace evidence model", url_configured=True)
        evidence = TraceEvidence.model_validate(payload)
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        return _blocked(f"trace evidence unavailable: {type(exc).__name__}", url_configured=True)
    incident = build_trace_incident(evidence)
    return _summary(
        mode="live",
        incident=incident,
        scope="operator-authenticated trace-incident summary only",
    )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = (
        _live(
            url=args.url,
            api_key=args.api_key,
            trace_id=args.trace_id,
            limit=args.limit,
            timeout=args.timeout,
        )
        if args.live
        else _fixture()
    )
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        print(f"trace incident: {report['status']} — {report.get('reason', report.get('scope', ''))}")
    return 2 if report["status"] == "blocked" else (0 if report["status"] in {"pass", "observed"} else 1)


if __name__ == "__main__":
    raise SystemExit(main())
