"""Check the versioned SLO and capacity forecast read models.

The default mode runs a deterministic fixture. ``--live`` queries the
operator-authenticated endpoints and emits only bounded status/count/coverage
fields. Missing telemetry is reported by the endpoint as ``no_data`` or
``insufficient_data``; an unavailable live endpoint is ``blocked`` (exit 2).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import httpx

from mas_core.observability.slo import (
    CAPACITY_FORECAST_SCHEMA,
    SLO_REPORT_SCHEMA,
    SLO_SERVICES,
    build_capacity_forecast,
    build_slo_report,
)

CHECK_SCHEMA = "aiat.slo-capacity-check.v1"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument("--live", action="store_true", help="query the operator SLO/capacity endpoints")
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
    parser.add_argument("--company-id", help="optional company scope UUID")
    parser.add_argument("--window-days", type=int, default=30)
    parser.add_argument("--forecast-days", type=int, default=30)
    parser.add_argument("--timeout", type=float, default=15.0)
    return parser


def _fixture() -> dict[str, Any]:
    observations = [
        {"service": service, "total": 100, "good": 100, "latency_ms": 10}
        for service in SLO_SERVICES
    ]
    slo = build_slo_report(observations, generated_at="2026-08-10T00:00:00+00:00")
    capacity = build_capacity_forecast(
        [
            {
                "event_count": 100,
                "total_tokens": 20_000,
                "total_cost_usd": 12.5,
                "first_event_at": "2026-08-01T00:00:00+00:00",
                "last_event_at": "2026-08-10T00:00:00+00:00",
            }
        ],
        window_days=30,
        forecast_days=30,
        budget_limit_usd=1000,
        budget_source="company_budgets",
        generated_at="2026-08-10T00:00:00+00:00",
    )
    return {
        "schema_version": CHECK_SCHEMA,
        "mode": "fixture",
        "status": "pass" if slo.status == "healthy" and capacity.status == "clear" else "fail",
        "slo_schema": slo.schema_version,
        "slo_status": slo.status,
        "slo_target_count": len(slo.statuses),
        "observed_service_count": slo.observed_service_count,
        "capacity_schema": capacity.schema_version,
        "capacity_status": capacity.status,
        "capacity_confidence": capacity.confidence,
        "observed_event_count": capacity.observed_event_count,
        "scope": "deterministic fixture; no database, worker, or provider state was changed",
    }


def _blocked(reason: str, *, url_configured: bool = False) -> dict[str, Any]:
    return {
        "schema_version": CHECK_SCHEMA,
        "mode": "live",
        "status": "blocked",
        "reason": reason,
        "url_configured": url_configured,
        "scope": "operator-authenticated SLO/capacity summaries only",
    }


def _live(
    *,
    url: str,
    api_key: str,
    company_id: str | None,
    window_days: int,
    forecast_days: int,
    timeout: float,
) -> dict[str, Any]:
    if not url.strip():
        return _blocked("missing live configuration: orchestrator URL")
    headers = {"X-API-Key": api_key} if api_key.strip() else {}
    params: dict[str, Any] = {"window_days": max(1, min(window_days, 3650))}
    if company_id:
        params["company_id"] = company_id
    try:
        slo_response = httpx.get(
            f"{url.rstrip('/')}/observability/slo",
            headers=headers,
            params=params,
            timeout=timeout,
        )
        slo_response.raise_for_status()
        slo_payload = slo_response.json()
        capacity_params = dict(params)
        capacity_params["forecast_days"] = max(1, min(forecast_days, 3650))
        capacity_response = httpx.get(
            f"{url.rstrip('/')}/observability/capacity/forecast",
            headers=headers,
            params=capacity_params,
            timeout=timeout,
        )
        capacity_response.raise_for_status()
        capacity_payload = capacity_response.json()
    except (httpx.HTTPError, ValueError) as exc:
        return _blocked(f"SLO/capacity endpoint unavailable: {type(exc).__name__}", url_configured=True)
    if not isinstance(slo_payload, dict) or slo_payload.get("schema_version") != SLO_REPORT_SCHEMA:
        return _blocked("orchestrator returned an invalid SLO report", url_configured=True)
    if not isinstance(capacity_payload, dict) or capacity_payload.get("schema_version") != CAPACITY_FORECAST_SCHEMA:
        return _blocked("orchestrator returned an invalid capacity forecast", url_configured=True)
    statuses = slo_payload.get("statuses")
    notices = slo_payload.get("notices")
    if not isinstance(statuses, list) or not isinstance(notices, list):
        return _blocked("SLO report omitted bounded statuses/notices", url_configured=True)
    return {
        "schema_version": CHECK_SCHEMA,
        "mode": "live",
        "status": "observed",
        "url_configured": True,
        "slo_status": str(slo_payload.get("status", "unknown")),
        "slo_target_count": len(statuses),
        "observed_service_count": int(slo_payload.get("observed_service_count") or 0),
        "slo_notice_codes": sorted(
            str(item.get("code")) for item in notices if isinstance(item, dict) and item.get("code")
        ),
        "capacity_status": str(capacity_payload.get("status", "unknown")),
        "capacity_confidence": str(capacity_payload.get("confidence", "none")),
        "observed_event_count": int(capacity_payload.get("observed_event_count") or 0),
        "scope": "operator-authenticated SLO/capacity summaries only",
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = (
        _live(
            url=args.url,
            api_key=args.api_key,
            company_id=args.company_id,
            window_days=args.window_days,
            forecast_days=args.forecast_days,
            timeout=args.timeout,
        )
        if args.live
        else _fixture()
    )
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        print(f"SLO/capacity: {report['status']} — {report.get('scope', report.get('reason', ''))}")
    if report["status"] == "blocked":
        return 2
    return 0 if report["status"] in {"pass", "observed"} else 1


if __name__ == "__main__":
    sys.exit(main())
