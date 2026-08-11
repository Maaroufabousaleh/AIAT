"""Check the bounded AIAT trace-evidence projection.

    The default fixture proves deterministic joining of task, usage,
    worker-transition, direct model/artifact/integration evidence, native
    transport/model/tool spans, and PM-inbound records without returning raw
    payloads. ``--live`` queries
the operator-authenticated read endpoint and emits only source counts,
coverage, and retention metadata. Missing configuration or an unavailable
endpoint is reported as ``blocked`` (exit code 2), never as a false pass.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import httpx

from mas_core.observability.trace_evidence import build_trace_evidence
from mas_core.observability.tracing import is_safe_trace_id

CHECK_SCHEMA = "aiat.trace-evidence-check.v1"
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


def _fixture() -> dict[str, Any]:
    evidence = build_trace_evidence(
        trace_id="fixture-trace-001",
        api_rows=[
            {
                "id": "fixture-api-001",
                "method": "GET",
                "route": "/health",
                "status_code": 200,
                "outcome": "success",
                "duration_ms": 4,
                "occurred_at": "2026-08-10T00:00:00+00:00",
            }
        ],
        task_rows=[
            {
                "task_id": "fixture-task-001",
                "agent_id": "tester",
                "team_id": "dept_qa",
                "status": "SUCCEEDED",
                "input": {"project_id": "fixture-project-001", "payload": "redacted"},
                "created_at": "2026-08-10T00:00:00+00:00",
            }
        ],
        usage_rows=[
            {
                "id": "fixture-usage-001",
                "project_id": "fixture-project-001",
                "event_type": "tool",
                "tool_name": "clock.now",
                "status": "success",
                "cost_usd": 0.01,
                "occurred_at": "2026-08-10T00:00:01+00:00",
            }
        ],
        transition_rows=[
            {
                "id": "fixture-transition-001",
                "run_id": "fixture-run-001",
                "to_state": "RUNNING",
                "created_at": "2026-08-10T00:00:02+00:00",
            }
        ],
        worker_usage_rows=[
            {
                "id": "fixture-worker-usage-001",
                "run_id": "fixture-run-001",
                "provider_id": "omniroute",
                "exact_model_id": "fixture-model",
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "cost_usd": 0.01,
                "created_at": "2026-08-10T00:00:03+00:00",
            }
        ],
        artifact_rows=[
            {
                "id": "fixture-artifact-001",
                "run_id": "fixture-run-001",
                "artifact_id": 1,
                "kind": "report",
                "sha256": "a" * 64,
                "size_bytes": 128,
                "created_at": "2026-08-10T00:00:04+00:00",
            }
        ],
        integration_rows=[
            {
                "id": "fixture-pm-event-001",
                "connection_id": "fixture-connection-001",
                "event_type": "issue.updated",
                "status": "PROCESSED",
                "received_at": "2026-08-10T00:00:05+00:00",
            }
        ],
        integration_evidence_rows=[
            {
                "id": "fixture-integration-evidence-001",
                "connection_id": "fixture-connection-001",
                "project_id": "fixture-project-001",
                "evidence_type": "pull_request.updated",
                "span_id": "fixture-span-integration-001",
                "created_at": "2026-08-10T00:00:06+00:00",
                "payload": {"redacted": True},
            }
        ],
        native_span_rows=[
            {
                "id": "fixture-native-span-001",
                "trace_id": "fixture-trace-001",
                "span_id": "fixture-native-span-id-001",
                "source_kind": "transport",
                "operation": "/health",
                "service": "orchestrator_api",
                "status": "success",
                "sampled": True,
                "started_at": "2026-08-10T00:00:00+00:00",
                "duration_ms": 4,
                "attributes_json": {"request_body": "redacted"},
            }
        ],
        generated_at="2026-08-10T00:00:03+00:00",
    )
    payload = evidence.model_dump(mode="json")
    return {
        "schema_version": CHECK_SCHEMA,
        "mode": "fixture",
        "status": "pass" if evidence.status == "observed" else "fail",
        "trace_schema": TRACE_SCHEMA,
        "item_count": evidence.item_count,
        "source_counts": evidence.source_counts,
        "coverage": evidence.coverage,
        "notice_codes": sorted(item["code"] for item in evidence.notices),
        "payload_shape": sorted(payload),
        "scope": "deterministic fixture; no database, worker, or provider state was changed",
    }


def _blocked(reason: str, *, url_configured: bool = False) -> dict[str, Any]:
    return {
        "schema_version": CHECK_SCHEMA,
        "mode": "live",
        "status": "blocked",
        "reason": reason,
        "url_configured": url_configured,
        "scope": "operator-authenticated trace evidence summary only",
    }


def _live(*, url: str, api_key: str, trace_id: str | None, limit: int, timeout: float) -> dict[str, Any]:
    if not url.strip():
        return _blocked("missing live configuration: orchestrator URL")
    if not trace_id or not is_safe_trace_id(trace_id):
        return _blocked("missing or invalid bounded trace ID", url_configured=True)
    endpoint = f"{url.rstrip('/')}/observability/traces/{trace_id}"
    headers = {"X-API-Key": api_key} if api_key.strip() else {}
    try:
        response = httpx.get(endpoint, headers=headers, params={"limit": max(1, min(limit, 300))}, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        return _blocked(f"trace evidence unavailable: {type(exc).__name__}", url_configured=True)
    if not isinstance(payload, dict) or payload.get("schema_version") != TRACE_SCHEMA:
        return _blocked("orchestrator returned an invalid trace evidence model", url_configured=True)
    source_counts = payload.get("source_counts")
    coverage = payload.get("coverage")
    retention = payload.get("retention")
    if not isinstance(source_counts, dict) or not isinstance(coverage, dict) or not isinstance(retention, dict):
        return _blocked("trace evidence omitted source coverage or retention", url_configured=True)
    return {
        "schema_version": CHECK_SCHEMA,
        "mode": "live",
        "status": str(payload.get("status", "unknown")),
        "url_configured": True,
        "item_count": int(payload.get("item_count") or 0),
        "source_counts": {str(key): int(value) for key, value in source_counts.items()},
        "coverage": {str(key): str(value) for key, value in coverage.items()},
        "retention": {
            "retention_days": retention.get("retention_days"),
            "sample_rate": retention.get("sample_rate"),
            "terminal_mode": retention.get("terminal_mode"),
            "source": retention.get("source"),
        },
        "notice_codes": sorted(
            str(item.get("code"))
            for item in payload.get("notices") or []
            if isinstance(item, dict) and item.get("code")
        ),
        "scope": "operator-authenticated trace evidence summary only",
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = _live(
        url=args.url,
        api_key=args.api_key,
        trace_id=args.trace_id,
        limit=args.limit,
        timeout=args.timeout,
    ) if args.live else _fixture()
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        print(f"trace evidence: {report['status']} — {report.get('reason', report.get('scope', ''))}")
    return 2 if report["status"] == "blocked" else (0 if report["status"] in {"pass", "observed", "not_found"} else 1)


if __name__ == "__main__":
    sys.exit(main())
