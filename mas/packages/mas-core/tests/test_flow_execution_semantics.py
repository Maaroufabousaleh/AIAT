"""Deterministic flow-engine traversal evidence."""

import json
import subprocess
import sys
from pathlib import Path


def test_flow_execution_semantics_fixture_covers_parallel_join_and_switch() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    result = subprocess.run(
        [sys.executable, "scripts/check_flow_execution_semantics.py", "--json"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    report = json.loads(result.stdout)

    assert report["schema_version"] == "aiat.flow-execution-semantics.v1"
    assert report["status"] == "pass"
    assert report["failed_checks"] == []
    assert report["licence_metadata_is_gate"] is False
    assert report["mutation_performed"] is False
    assert report["worker_dispatch_performed"] is False
    assert report["checks"]["parallel_join"]["cases"]["join_once"] == ["join"]
    assert report["checks"]["parallel_join"]["cases"]["end_after_join"] == ["end"]
    assert report["checks"]["switch_routing"]["cases"] == {
        "activation": ["switch"],
        "ok": ["ok"],
        "fail": ["fail"],
    }
    assert report["checks"]["switch_routing"]["unknown_blocked"] is True
