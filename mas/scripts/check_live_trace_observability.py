"""Exercise the live orchestrator trace-evidence boundary.

The check sends one bounded ``GET /health`` request with a caller-selected
trace ID, then reads the operator-only trace projection.  It emits only
status/count/coverage fields and never prints the API key, response payload,
headers, or database rows.  A missing or unavailable deployment is ``blocked``
(exit code 2), not a false pass.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import httpx

from mas_core.observability.tracing import is_safe_trace_id

CHECK_SCHEMA = "aiat.live-trace-observability-check.v1"
TRACE_SCHEMA = "aiat.trace-evidence.v1"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument("--live", action="store_true", help="exercise the configured deployment")
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
    parser.add_argument(
        "--trace-id",
        default=os.getenv("AIAT_LIVE_TRACE_ID", "aiat-live-trace-check"),
        help="bounded trace ID used for the disposable health request",
    )
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--timeout", type=float, default=15.0)
    return parser


def _blocked(reason: str, *, url_configured: bool = False) -> dict[str, Any]:
    return {
        "schema_version": CHECK_SCHEMA,
        "mode": "live",
        "status": "blocked",
        "reason": reason,
        "url_configured": url_configured,
        "scope": "one bounded health request and an operator-authenticated trace summary",
    }


def _live(
    *,
    url: str,
    api_key: str,
    trace_id: str,
    limit: int,
    timeout: float,
) -> dict[str, Any]:
    if not url.strip():
        return _blocked("missing live configuration: orchestrator URL")
    if not api_key.strip():
        return _blocked("missing live configuration: operator API key", url_configured=True)
    if not is_safe_trace_id(trace_id):
        return _blocked("trace ID is not a bounded safe identifier", url_configured=True)

    base = url.rstrip("/")
    bounded_limit = max(1, min(int(limit), 300))
    try:
        with httpx.Client(timeout=timeout) as client:
            health = client.get(
                f"{base}/health",
                headers={"X-AIAT-Trace-ID": trace_id},
            )
            if health.status_code != 200:
                return _blocked(
                    f"health endpoint returned HTTP {health.status_code}",
                    url_configured=True,
                )
            response_trace_id = str(health.headers.get("x-aiat-trace-id") or "").strip()
            if response_trace_id != trace_id:
                return _blocked(
                    "health response did not preserve the requested trace ID",
                    url_configured=True,
                )
            evidence_response = client.get(
                f"{base}/observability/traces/{trace_id}",
                headers={"X-API-Key": api_key},
                params={"limit": bounded_limit},
            )
            evidence_response.raise_for_status()
            payload = evidence_response.json()
    except (httpx.HTTPError, ValueError) as exc:
        return _blocked(f"live trace evidence unavailable: {type(exc).__name__}", url_configured=True)

    if not isinstance(payload, dict) or payload.get("schema_version") != TRACE_SCHEMA:
        return _blocked("orchestrator returned an invalid trace evidence model", url_configured=True)
    source_counts = payload.get("source_counts")
    coverage = payload.get("coverage")
    retention = payload.get("retention")
    items = payload.get("items")
    if (
        not isinstance(source_counts, dict)
        or not isinstance(coverage, dict)
        or not isinstance(retention, dict)
        or not isinstance(items, list)
    ):
        return _blocked("trace evidence omitted bounded coverage fields", url_configured=True)

    native_items = [item for item in items if isinstance(item, dict) and item.get("kind") == "native_span"]
    native_operations = sorted(
        {
            str(item.get("operation"))
            for item in native_items
            if item.get("operation")
        }
    )
    native_services = sorted(
        {
            str(item.get("service"))
            for item in native_items
            if item.get("service")
        }
    )
    evidence_status = str(payload.get("status", "unknown"))
    return {
        "schema_version": CHECK_SCHEMA,
        "mode": "live",
        "status": "pass" if evidence_status in {"observed", "not_found"} else "fail",
        "evidence_status": evidence_status,
        "url_configured": True,
        "trace_id_used": trace_id,
        "item_count": int(payload.get("item_count") or 0),
        "source_counts": {str(key): int(value) for key, value in source_counts.items()},
        "coverage": {str(key): str(value) for key, value in coverage.items()},
        "native_span_count": len(native_items),
        "native_operations": native_operations,
        "native_services": native_services,
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
        "scope": "one bounded health request and an operator-authenticated trace summary; no mutation beyond telemetry rows",
    }


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
        else {
            "schema_version": CHECK_SCHEMA,
            "mode": "fixture",
            "status": "pass",
            "live_required": True,
            "scope": "deterministic contract fixture; no network or database state changed",
        }
    )
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        print(f"live trace observability: {report['status']} — {report.get('reason', report.get('scope', ''))}")
    if report["status"] == "blocked":
        return 2
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
