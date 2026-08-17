"""Check the bounded AIAT trace-retention planning projection.

The default fixture exercises metadata-only retention decisions. ``--live``
queries the operator-authenticated read endpoint and emits bounded policy and
count summaries. Neither mode connects to a destructive action: the report
always states ``mutation_performed: false``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from mas_core.observability.retention import (
    TRACE_RETENTION_PLAN_SCHEMA,
    plan_native_span_retention,
)
from mas_core.observability.trace_evidence import TraceRetentionPolicy
from mas_core.observability.tracing import is_safe_trace_id

CHECK_SCHEMA = "aiat.trace-retention-check.v1"
EVALUATED_AT = datetime(2026, 1, 1, tzinfo=UTC)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument("--live", action="store_true", help="query the operator retention-plan endpoint")
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
    parser.add_argument("--trace-id", help="optional bounded trace/message ID to query in live mode")
    parser.add_argument("--limit", type=int, default=1_000)
    parser.add_argument("--timeout", type=float, default=15.0)
    return parser


def _rows() -> list[dict[str, Any]]:
    return [
        {
            "id": "span-expired",
            "trace_id": "retention-fixture-trace",
            "source_kind": "transport",
            "started_at": (EVALUATED_AT - timedelta(days=45)).isoformat(),
        },
        {
            "id": "span-active",
            "trace_id": "retention-fixture-trace",
            "source_kind": "tool",
            "started_at": (EVALUATED_AT - timedelta(days=2)).isoformat(),
        },
        {
            "id": "span-explicit-expiry",
            "trace_id": "retention-fixture-trace",
            "source_kind": "mail",
            "started_at": (EVALUATED_AT - timedelta(days=2)).isoformat(),
            "retention_until": (EVALUATED_AT - timedelta(minutes=1)).isoformat(),
        },
        {
            "id": "span-legal-hold",
            "trace_id": "retention-fixture-trace",
            "source_kind": "audit",
            "started_at": (EVALUATED_AT - timedelta(days=45)).isoformat(),
            "attributes_json": {"legal_hold": True},
        },
        {"id": "span-invalid", "source_kind": "audit"},
    ]


def build_report() -> dict[str, object]:
    delete_plan = plan_native_span_retention(
        _rows(),
        TraceRetentionPolicy(retention_days=30, terminal_mode="delete"),
        evaluated_at=EVALUATED_AT,
    )
    archive_plan = plan_native_span_retention(
        _rows()[:1],
        TraceRetentionPolicy(retention_days=30, terminal_mode="archive"),
        evaluated_at=EVALUATED_AT,
    )
    safe = (
        delete_plan.counts == {
            "retain": 2,
            "archive": 0,
            "delete": 2,
            "invalid": 1,
            "legal_hold": 1,
        }
        and delete_plan.deletion_ids == ("span-expired", "span-explicit-expiry")
        and archive_plan.counts == {
            "retain": 0,
            "archive": 1,
            "delete": 0,
            "invalid": 0,
            "legal_hold": 0,
        }
        and not any(candidate.disposition == "delete" for candidate in archive_plan.candidates)
    )
    return {
        "schema_version": CHECK_SCHEMA,
        "plan_schema": TRACE_RETENTION_PLAN_SCHEMA,
        "status": "pass" if safe else "fail",
        "delete_plan": delete_plan.as_dict(),
        "archive_plan": archive_plan.as_dict(),
        "live_enforcement_status": "not_checked",
        "mutation_performed": False,
        "licence_metadata_is_gate": False,
        "scope": "deterministic fixture; no database, network, or provider state changed",
    }


def _blocked(reason: str, *, url_configured: bool = False) -> dict[str, Any]:
    return {
        "schema_version": CHECK_SCHEMA,
        "mode": "live",
        "status": "blocked",
        "reason": reason,
        "url_configured": url_configured,
        "mutation_performed": False,
        "scope": "operator-authenticated read-only retention plan; no archive/delete mutation",
    }


def _live(
    *,
    url: str,
    api_key: str,
    trace_id: str | None,
    limit: int,
    timeout: float,
) -> dict[str, Any]:
    if not url.strip():
        return _blocked("missing live configuration: orchestrator URL")
    normalized_trace_id = str(trace_id).strip() if trace_id is not None else None
    if normalized_trace_id and not is_safe_trace_id(normalized_trace_id):
        return _blocked("invalid bounded trace ID", url_configured=True)

    bounded_limit = max(1, min(int(limit), 10_000))
    endpoint = f"{url.rstrip('/')}/observability/retention/plan"
    params: dict[str, str | int] = {"limit": bounded_limit}
    if normalized_trace_id:
        params["trace_id"] = normalized_trace_id
    headers = {"X-API-Key": api_key} if api_key.strip() else {}
    try:
        response = httpx.get(endpoint, headers=headers, params=params, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        return _blocked(f"retention plan unavailable: {type(exc).__name__}", url_configured=True)

    if not isinstance(payload, dict):
        return _blocked("orchestrator returned an invalid retention plan model", url_configured=True)
    if payload.get("schema_version") != TRACE_RETENTION_PLAN_SCHEMA:
        return _blocked("orchestrator returned an invalid retention plan schema", url_configured=True)
    if payload.get("mode") != "read-only-plan":
        return _blocked("retention plan did not declare read-only mode", url_configured=True)
    if payload.get("mutation_performed") is not False:
        return _blocked("retention plan did not prove mutation_performed=false", url_configured=True)
    counts = payload.get("counts")
    policy = payload.get("policy")
    if not isinstance(counts, dict) or not isinstance(policy, dict):
        return _blocked("retention plan omitted counts or policy metadata", url_configured=True)
    try:
        normalized_counts = {
            key: int(counts.get(key, 0))
            for key in ("retain", "archive", "delete", "invalid", "legal_hold")
        }
    except (TypeError, ValueError):
        return _blocked("retention plan returned invalid count metadata", url_configured=True)
    return {
        "schema_version": CHECK_SCHEMA,
        "mode": "live",
        "status": "observed",
        "plan_schema": TRACE_RETENTION_PLAN_SCHEMA,
        "url_configured": True,
        "trace_id": payload.get("trace_id"),
        "counts": normalized_counts,
        "candidate_count": sum(
            normalized_counts[key] for key in ("retain", "archive", "delete", "invalid")
        ),
        "policy": {
            "retention_days": policy.get("retention_days"),
            "sample_rate": policy.get("sample_rate"),
            "terminal_mode": policy.get("terminal_mode"),
            "source": policy.get("source"),
        },
        "notice_count": len(payload.get("notices") or []) if isinstance(payload.get("notices"), list) else 0,
        "mutation_performed": False,
        "scope": "operator-authenticated read-only retention plan; no archive/delete mutation",
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = _live(
        url=args.url,
        api_key=args.api_key,
        trace_id=args.trace_id,
        limit=args.limit,
        timeout=args.timeout,
    ) if args.live else build_report()
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        print(f"trace retention: {report['status']} — {report.get('reason', report.get('scope', ''))}")
    return 2 if report["status"] == "blocked" else (0 if report["status"] in {"pass", "observed"} else 1)


if __name__ == "__main__":
    sys.exit(main())
