"""Run the deterministic native trace-span safety fixture.

This check exercises the span normalizer and its payload-free attribute
boundary without requiring Postgres or a live tracing backend.  It does not
claim mail-edge or live retention evidence.
"""

from __future__ import annotations

import argparse
import json
import sys

from mas_core.observability.native_spans import (
    NATIVE_TRACE_SPAN_SCHEMA,
    build_native_trace_span,
)

CHECK_SCHEMA = "aiat.native-trace-span-check.v1"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args(argv)
    span = build_native_trace_span(
        trace_id="native-fixture-trace",
        span_id="native-fixture-span",
        source_kind="transport",
        operation="/health",
        service="orchestrator_api",
        status="success",
        duration_ms=4,
        attributes={
            "method": "GET",
            "status_code": 200,
            "request_body": "must-drop",
            "authorization": "must-drop",
        },
    )
    safe = span["attributes"] == {"method": "GET", "status_code": 200}
    report = {
        "schema_version": CHECK_SCHEMA,
        "span_schema": NATIVE_TRACE_SPAN_SCHEMA,
        "status": "pass" if safe else "fail",
        "source_kind": span["source_kind"],
        "duration_ms": span["duration_ms"],
        "safe_attribute_keys": sorted(span["attributes"]),
        "mail_edge_status": "not_checked",
        "live_retention_status": "not_checked",
        "licence_metadata_is_gate": False,
        "scope": "deterministic fixture; no database, network, or provider state changed",
    }
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        print(f"native trace spans: {report['status']} — {report['scope']}")
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
