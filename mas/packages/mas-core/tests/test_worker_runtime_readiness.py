"""Tests for the runtime prerequisite/readiness boundary."""

from __future__ import annotations

import json
import subprocess
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "check_worker_runtime_readiness.py"


def _run(*args: str) -> dict:
    result = subprocess.run(
        [sys.executable, "scripts/check_worker_runtime_readiness.py", "--json", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.stdout
    return {"returncode": result.returncode, "report": json.loads(result.stdout)}


def _load_runner():
    spec = spec_from_file_location("worker_runtime_readiness_runner", SCRIPT)
    assert spec and spec.loader
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_static_runtime_readiness_is_declaration_only() -> None:
    result = _run()
    report = result["report"]

    assert report["schema_version"] == "aiat.worker-runtime-readiness.v1"
    assert report["mode"] == "static"
    assert report["status"] == "pass"
    assert report["worker_count"] == 39
    assert result["returncode"] == 0
    assert report["certification_boundary"]["security_scan"] == "not_checked"


def test_live_runtime_readiness_reports_missing_required_packages_without_certifying_workers() -> None:
    result = _run("--live")
    report = result["report"]

    assert report["mode"] == "live"
    assert report["certification_boundary"]["live_worker_run"] == "not_checked"
    if report["status"] == "blocked":
        assert report["required_runtime_blockers"]
        assert report["reason"] == "required runtime package imports are unavailable"
        assert result["returncode"] == 2


def test_compose_local_runtime_readiness_probes_running_image(monkeypatch) -> None:
    runner = _load_runner()
    monkeypatch.setattr(runner.shutil, "which", lambda name: "/usr/bin/docker")

    class _Result:
        returncode = 0
        stdout = json.dumps(
            {
                "agent_framework": False,
                "autogen_agentchat": False,
                "autogen_core": False,
                "crewai": True,
                "langgraph": True,
                "letta": False,
            }
        )
        stderr = ""

    monkeypatch.setattr(runner.subprocess, "run", lambda *args, **kwargs: _Result())
    report = runner.compose_local_reconcile()

    assert report["mode"] == "live"
    assert report["environment"] == "compose-local"
    assert report["status"] == "pass"
    assert report["required_runtime_blockers"] == []
    assert report["runtime_probe"]["transport"] == "docker-exec"
