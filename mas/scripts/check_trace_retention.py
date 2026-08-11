"""Run the deterministic trace-retention planning fixture.

The command exercises metadata-only retention decisions.  It does not connect
to Postgres, delete spans, archive payloads, or claim live storage/recovery
evidence.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta

from mas_core.observability.retention import (
    TRACE_RETENTION_PLAN_SCHEMA,
    plan_native_span_retention,
)
from mas_core.observability.trace_evidence import TraceRetentionPolicy

CHECK_SCHEMA = "aiat.trace-retention-check.v1"
EVALUATED_AT = datetime(2026, 1, 1, tzinfo=UTC)


def _rows() -> list[dict[str, str]]:
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
        delete_plan.counts == {"retain": 1, "archive": 0, "delete": 2, "invalid": 1}
        and delete_plan.deletion_ids == ("span-expired", "span-explicit-expiry")
        and archive_plan.counts == {"retain": 0, "archive": 1, "delete": 0, "invalid": 0}
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args(argv)
    report = build_report()
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        print(f"trace retention: {report['status']} — {report['scope']}")
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
