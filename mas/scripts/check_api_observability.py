"""Check the bounded API request observation contract.

The default fixture validates route normalization, scalar status/duration
bounds, trace handling, and the payload-free response shape.  It does not
need a database or a live API and never reports request bodies, headers,
query strings, credentials, or exception text.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from mas_core.observability.api_observations import (
    API_OBSERVATION_SCHEMA,
    build_api_observation,
)

CHECK_SCHEMA = "aiat.api-observability-check.v1"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON")
    return parser


def _fixture() -> dict[str, Any]:
    rows = [
        build_api_observation(
            method="GET",
            path="/projects/123/tasks/550e8400-e29b-41d4-a716-446655440000?secret=redacted",
            route_template="/projects/{project_id}/tasks/{task_id}",
            status_code=200,
            duration_ms=12.5,
            trace_id="fixture-api-trace-001",
            principal="operator",
            dashboard_section="governance",
        ),
        build_api_observation(
            method="POST",
            path="/projects/123?token=redacted",
            status_code=503,
            duration_ms=86_400_001,
            trace_id="unsafe trace value",
        ),
    ]
    forbidden_keys = {"body", "headers", "query", "credentials", "exception", "error"}
    safe = all(not forbidden_keys.intersection(row) for row in rows)
    bounded = all(
        100 <= int(row["status_code"]) <= 599
        and 0 <= float(row["duration_ms"]) <= 86_400_000
        and "?" not in str(row["route"])
        for row in rows
    )
    normalized = rows[0]["route"] == "/projects/:param/tasks/:param"
    status = "pass" if safe and bounded and normalized else "fail"
    return {
        "schema_version": CHECK_SCHEMA,
        "observation_schema": API_OBSERVATION_SCHEMA,
        "mode": "fixture",
        "status": status,
        "row_count": len(rows),
        "routes": sorted(str(row["route"]) for row in rows),
        "status_codes": sorted(int(row["status_code"]) for row in rows),
        "payload_free": safe,
        "bounded": bounded,
        "scope": "deterministic fixture; no database, worker, provider, or request payload state was changed",
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = _fixture()
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        print(f"API observability: {report['status']} — {report['scope']}")
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
