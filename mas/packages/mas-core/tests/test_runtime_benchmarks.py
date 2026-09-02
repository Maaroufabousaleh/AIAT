"""Tests for the read-only orchestrator runtime benchmark probe."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "check_runtime_benchmarks.py"


def _load_runner():
    spec = importlib.util.spec_from_file_location("check_runtime_benchmarks", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_parser_prefers_operator_key_over_legacy_aliases(monkeypatch) -> None:
    runner = _load_runner()
    monkeypatch.setenv("AIAT_OPERATOR_API_KEY", "operator-key")
    monkeypatch.setenv("AIAT_API_KEY", "legacy-key")
    monkeypatch.setenv("MAS_API_KEY", "service-key")

    args = runner._parser().parse_args([])

    assert args.api_key == "operator-key"


def test_missing_live_configuration_is_blocked_without_secret_output() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--live", "--json", "--api-key", "secret-value"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "secret-value" not in result.stdout
    report = json.loads(result.stdout)
    assert report["schema_version"] == "aiat.runtime-benchmark-readiness.v1"
    assert report["status"] == "blocked"
    assert report["certification_boundary"]["live_worker_run"] == "not_checked"


def test_without_live_flag_is_declaration_only() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--json"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    report = json.loads(result.stdout)
    assert report["mode"] == "static"
    assert report["status"] == "pass"
    assert report["certification_boundary"]["package_benchmark"] == "not_checked"


def test_runtime_benchmark_classifies_completed_and_unavailable(monkeypatch) -> None:
    runner = _load_runner()

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    monkeypatch.setattr(
        runner.httpx,
        "get",
        lambda *args, **kwargs: Response(
            {
                "runtimes": [
                    {"id": "langgraph", "status": "available", "policy": {"sandbox_required": "gvisor"}},
                    {"id": "crewai", "status": "unavailable", "policy": {"sandbox_required": "gvisor"}},
                ]
            }
        ),
    )

    posted_configs = {}

    def post(_url, *, json, **_kwargs):
        posted_configs[json["runtime_tier"]] = json["runtime_config"]
        if json["runtime_tier"] == "langgraph":
            return Response(
                {
                    "runtime_tier": "langgraph",
                    "status": "dry_run_completed",
                    "benchmark_results": {"elapsed_ms": 1.2, "tasks_run": 1, "tasks_passed": 1},
                }
            )
        return Response({"runtime_tier": "crewai", "status": "package_unavailable", "missing_packages": ["crewai"]})

    monkeypatch.setattr(runner.httpx, "post", post)
    report = runner.inspect_live(
        url="http://orchestrator.invalid",
        api_key="secret-value",
        runtime_ids=("langgraph", "crewai"),
        timeout=1,
    )
    assert report["status"] == "blocked"
    assert report["runtimes"][0]["status"] == "dry_run_completed"
    assert report["runtimes"][1]["missing_packages"] == ["crewai"]
    assert posted_configs["langgraph"]["state_schema"] == {"messages": []}
    assert posted_configs["langgraph"]["checkpointer"] == "memory"
    assert posted_configs["crewai"]["process"] == "sequential"
    assert posted_configs["crewai"]["crew_config"]["agents"][0]["role"]
    assert "secret-value" not in json.dumps(report)


def test_runtime_benchmark_timeout_is_blocked_not_failed(monkeypatch) -> None:
    runner = _load_runner()

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    monkeypatch.setattr(
        runner.httpx,
        "get",
        lambda *args, **kwargs: Response(
            {"runtimes": [{"id": "crewai", "status": "available", "policy": {}}]}
        ),
    )
    monkeypatch.setattr(
        runner.httpx,
        "post",
        lambda *args, **kwargs: Response(
            {
                "runtime_tier": "crewai",
                "status": "benchmark_timeout",
                "benchmark_results": {
                    "elapsed_ms": 100.0,
                    "tasks_run": 0,
                    "tasks_passed": 0,
                    "timeout_seconds": 0.1,
                },
            }
        ),
    )

    report = runner.inspect_live(
        url="http://orchestrator.invalid",
        api_key="secret-value",
        runtime_ids=("crewai",),
        timeout=1,
    )
    assert report["status"] == "blocked"
    assert report["runtimes"][0]["status"] == "benchmark_timeout"
    assert report["runtimes"][0]["benchmark"]["timeout_seconds"] == 0.1
    assert "secret-value" not in json.dumps(report)
