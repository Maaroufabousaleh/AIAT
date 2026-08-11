"""Static and fail-closed Docker sandbox readiness evidence."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "check_sandbox_runtime_readiness.py"


def test_static_sandbox_declarations_pass() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--json"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["schema_version"] == "aiat.sandbox-runtime-readiness.v1"
    assert report["status"] == "pass"
    assert report["worker_count"] == 39
    assert report["hardened_worker_count"] > 0
    assert "live" not in report


def test_live_sandbox_readiness_is_blocked_without_docker_engine() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--live", "--json"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2, result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "pass"
    assert report["live"]["status"] == "blocked"
    assert report["live"]["sandbox_profile"] == "gvisor"


def test_live_sandbox_probe_requires_runsc_and_separates_smoke(monkeypatch) -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location("check_sandbox_runtime_readiness", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    monkeypatch.setattr(module, "_docker_runtimes", lambda: ({"runc"}, None))
    missing = module.inspect_live()
    assert missing["status"] == "blocked"
    assert "no runc fallback" in missing["reason"]

    monkeypatch.setattr(module, "_docker_runtimes", lambda: ({"runsc"}, None))
    registered = module.inspect_live()
    assert registered["status"] == "pass"
    assert registered["smoke"] == "not_checked"

    smoke = module.inspect_live(smoke=True)
    assert smoke["status"] == "blocked"
    assert "requires --image" in smoke["reason"]
