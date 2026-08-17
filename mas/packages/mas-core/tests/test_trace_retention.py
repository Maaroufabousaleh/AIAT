from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from mas_core.observability.retention import (
    TraceRetentionPlanResponse,
    plan_native_span_retention,
)
from mas_core.observability.trace_evidence import TraceRetentionPolicy


def test_retention_planner_is_explicit_and_non_mutating() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    rows = [
        {
            "id": "old",
            "trace_id": "trace-1",
            "source_kind": "tool",
            "started_at": now - timedelta(days=31),
        },
        {
            "id": "new",
            "trace_id": "trace-1",
            "source_kind": "tool",
            "started_at": now - timedelta(days=1),
        },
        {"id": "invalid", "source_kind": "tool"},
    ]

    plan = plan_native_span_retention(
        rows,
        TraceRetentionPolicy(retention_days=30, terminal_mode="delete"),
        evaluated_at=now,
    )

    assert plan.counts == {"retain": 1, "archive": 0, "delete": 1, "invalid": 1}
    assert plan.deletion_ids == ("old",)
    assert rows[0]["id"] == "old"
    assert plan.candidates[-1].disposition == "invalid"


def test_retention_archive_mode_never_returns_deletion_ids() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    plan = plan_native_span_retention(
        [
            {
                "id": "old",
                "trace_id": "trace-1",
                "source_kind": "audit",
                "started_at": now - timedelta(days=90),
            }
        ],
        TraceRetentionPolicy(retention_days=30, terminal_mode="archive"),
        evaluated_at=now,
    )

    assert plan.counts == {"retain": 0, "archive": 1, "delete": 0, "invalid": 0}
    assert plan.deletion_ids == ()


def test_retention_plan_response_contract_is_versioned_and_non_mutating() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    plan = plan_native_span_retention(
        [
            {
                "id": "old",
                "trace_id": "trace-1",
                "source_kind": "tool",
                "started_at": now - timedelta(days=31),
            }
        ],
        TraceRetentionPolicy(retention_days=30, terminal_mode="delete"),
        evaluated_at=now,
    )
    payload = plan.as_dict()
    payload.update(
        {
            "mode": "read-only-plan",
            "mutation_performed": False,
            "trace_id": "trace-1",
            "scope": "trace",
        }
    )

    response = TraceRetentionPlanResponse.model_validate(payload)

    assert response.schema_version == "aiat.trace-retention-plan.v1"
    assert response.counts.delete == 1
    assert response.deletion_ids == ["old"]
    assert response.mutation_performed is False

    with pytest.raises(ValueError):
        TraceRetentionPlanResponse.model_validate(
            {**payload, "mutation_performed": True}
        )


def test_trace_retention_checker_is_deterministic() -> None:
    script = Path(__file__).resolve().parents[3] / "scripts" / "check_trace_retention.py"
    first = subprocess.run(
        [sys.executable, str(script), "--json"],
        check=True,
        capture_output=True,
        text=True,
    )
    second = subprocess.run(
        [sys.executable, str(script), "--json"],
        check=True,
        capture_output=True,
        text=True,
    )
    first_payload = json.loads(first.stdout)
    second_payload = json.loads(second.stdout)
    assert first_payload == second_payload
    assert first_payload["status"] == "pass"
    assert first_payload["mutation_performed"] is False
    assert first_payload["licence_metadata_is_gate"] is False
