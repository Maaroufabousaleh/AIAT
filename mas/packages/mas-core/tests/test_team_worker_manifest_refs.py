"""Team-runner to worker-manifest declaration reconciliation tests."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from mas_core.worker_registry.team_manifest_refs import (
    reconcile_team_worker_manifest_refs,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
MAS_ROOT = REPO_ROOT


def test_checked_in_team_agents_have_explicit_manifest_references() -> None:
    report = reconcile_team_worker_manifest_refs(
        teams_dir=MAS_ROOT / "teams",
        workers_dir=MAS_ROOT / "workers",
    )
    assert report["status"] == "pass", report["errors"]
    assert report["team_count"] == 11
    assert report["agent_count"] == 39
    assert report["worker_manifest_count"] == 39
    assert report["licence_metadata_is_gate"] is False


def test_missing_or_mismatched_references_fail_closed(tmp_path: Path) -> None:
    teams = tmp_path / "teams"
    workers = tmp_path / "workers"
    teams.mkdir()
    workers.mkdir()
    (workers / "known.yaml").write_text("metadata:\n  id: known\n", encoding="utf-8")
    (teams / "team.yaml").write_text(
        """team_id: fixture
admin:
  agent_id: admin
workers:
  - agent_id: worker
    worker_manifest_ref: known
""",
        encoding="utf-8",
    )
    report = reconcile_team_worker_manifest_refs(teams_dir=teams, workers_dir=workers)
    assert report["status"] == "fail"
    errors = set(report["errors"])
    assert any("admin: worker_manifest_ref is required" in error for error in errors)
    assert any("worker_manifest_ref 'known' must equal agent_id 'worker'" in error for error in errors)


def test_checker_cli_is_read_only_and_secret_safe() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/check_team_worker_manifest_refs.py", "--json"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["schema_version"] == "aiat.team-worker-manifest-refs-check.v1"
    assert report["reconciliation_schema"] == "aiat.team-worker-manifest-reconciliation.v1"
    assert report["status"] == "pass"
    assert report["no_mutation"] is True
    assert "api_key" not in result.stdout
