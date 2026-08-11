"""Tests for the guarded flow-instance recovery evidence probe."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "check_flow_instance_recovery.py"


def _load_runner():
    spec = importlib.util.spec_from_file_location("check_flow_instance_recovery", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_static_flow_recovery_probe_is_declaration_only() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--json"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    report = json.loads(result.stdout)
    assert report["schema_version"] == "aiat.flow-instance-recovery-readiness.v1"
    assert report["mode"] == "static"
    assert report["status"] == "pass"


def test_live_flow_recovery_requires_id_and_confirmation() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--live", "--json", "--url", "http://orchestrator.invalid"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    report = json.loads(result.stdout)
    assert report["reason"] == "missing flow instance ID"

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--live",
            "--json",
            "--url",
            "http://orchestrator.invalid",
            "--instance-id",
            "instance-1",
            "--action",
            "pause",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    report = json.loads(result.stdout)
    assert "--confirm" in report["reason"]


def test_live_flow_recovery_observes_status_and_validates_confirmed_action(monkeypatch) -> None:
    runner = _load_runner()

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    instance_running = {
        "id": "instance-1",
        "flow_id": "flow-1",
        "project_id": "project-1",
        "status": "RUNNING",
        "active_node_ids": ["task-1"],
        "retry_count": 0,
        "context_json": {"last_safe_node_id": "task-1"},
    }
    instance_paused = {**instance_running, "status": "PAUSED"}
    executions = [{"status": "RUNNING"}]
    calls = {"get": 0}

    def get(_url, **_kwargs):
        calls["get"] += 1
        # Status: instance, executions. Confirmed pause: before instance,
        # before executions, after instance, after executions.
        if calls["get"] in {1, 3}:
            return Response(instance_running)
        if calls["get"] == 5:
            return Response(instance_paused)
        return Response(executions)

    monkeypatch.setattr(runner.httpx, "get", get)
    monkeypatch.setattr(runner.httpx, "post", lambda *_args, **_kwargs: Response(instance_paused))

    status = runner.inspect_live(
        url="http://orchestrator.invalid",
        api_key="secret-value",
        instance_id="instance-1",
        action="status",
        confirm=False,
        timeout=1,
    )
    assert status["status"] == "pass"
    assert status["before"]["status"] == "RUNNING"
    assert "secret-value" not in json.dumps(status)

    action = runner.inspect_live(
        url="http://orchestrator.invalid",
        api_key="secret-value",
        instance_id="instance-1",
        action="pause",
        confirm=True,
        timeout=1,
    )
    assert action["status"] == "pass"
    assert action["after"]["status"] == "PAUSED"
