"""Tests for the bounded metric-series release verifier."""

from __future__ import annotations

import json
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "check_metric_series_budget.py"


def _checker_module():
    spec = importlib.util.spec_from_file_location("aiat_metric_series_checker", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_metric_series_fixture_is_bounded_for_large_project_population() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--json", "--synthetic-projects", "10000"],
        cwd=SCRIPT.parents[1],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["schema_version"] == "aiat.metric-series-budget-check.v1"
    assert report["status"] == "pass"
    assert report["synthetic_project_count"] == 10_000
    assert report["project_id_label_present"] is False
    assert report["family_counts"].get("mas_project_state", 0) <= 32
    assert report["label_inventory"]["mas_project_state"] == ["state"]
    assert report["declared_label_inventory"]["mas_messages"] == [
        "direction",
        "msg_type",
        "team",
    ]
    assert all(
        policy["classification"] == "bounded"
        for family in report["label_policies"].values()
        for policy in family.values()
    )


def test_metric_series_live_without_url_is_blocked() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--live", "--json"],
        cwd=SCRIPT.parents[1],
        env={
            key: value
            for key, value in os.environ.items()
            if key not in {"AIAT_ORCHESTRATOR_URL", "ORCHESTRATOR_API_URL"}
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    report = json.loads(result.stdout)
    assert report["status"] == "blocked"
    assert report["reason"] == "missing live configuration: orchestrator URL"


def test_live_histogram_created_sample_stays_in_declared_family() -> None:
    """Prometheus' synthetic histogram timestamp is not a new AIAT family."""

    checker = _checker_module()
    family_counts, labels = checker._parse_scrape(
        "\n".join(
            [
                "# HELP mas_infra_lead_time histogram",
                "# TYPE mas_infra_lead_time histogram",
                'mas_infra_lead_time_bucket{le="10"} 1',
                'mas_infra_lead_time_bucket{le="+Inf"} 1',
                "mas_infra_lead_time_sum 2",
                "mas_infra_lead_time_count 1",
                'mas_infra_lead_time_created 1700000000',
            ]
        )
    )
    assert "mas_infra_lead_time_created" not in family_counts
    assert family_counts["mas_infra_lead_time"] == 5
    assert labels["mas_infra_lead_time"] == {"le"}
