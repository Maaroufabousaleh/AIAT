"""Static network-boundary contract tests for the release verifier."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import yaml

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "check_network_boundary.py"
COMPOSE = Path(__file__).resolve().parents[3] / "infra" / "compose" / "docker-compose.yml"


def _load_boundary_module():
    spec = importlib.util.spec_from_file_location("check_network_boundary", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_current_compose_passes_network_boundary_contract() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--compose", str(COMPOSE), "--json"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "pass"
    assert "opencode-runtime" in report["protected_services"]
    assert report["team_environment"]["team-exec-ceo"]["identity"] == [
        "AIAT_CEO_API_KEY"
    ]
    assert report["boundary_policy"]["schema_version"] == "aiat.network-boundary-policy.v1"
    assert report["boundary_policy"]["protected"][-1] == {
        "service": "opencode-runtime",
        "network": "internal",
        "port": 4096,
    }
    assert report["boundary_policy"]["allowed"][0] == {
        "service": "message-router",
        "network": "workers",
        "port": 8001,
    }
    assert report["boundary_policy"]["networks"] == {
        "internal": {"internal": True},
        "workers": {"internal": True},
    }


def test_network_boundary_contract_rejects_runner_data_plane_access(tmp_path: Path) -> None:
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    compose["services"]["team-office-cio"]["networks"] = ["workers", "internal"]
    compose["services"]["team-office-cio"]["environment"]["PGBOUNCER_DSN"] = "redacted"
    path = tmp_path / "compose.yml"
    path.write_text(yaml.safe_dump(compose, sort_keys=False), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--compose", str(path), "--json"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert report["status"] == "fail"
    assert any("team-office-cio" in error for error in report["errors"])
    assert any("PGBOUNCER_DSN" in error for error in report["errors"])


def test_network_boundary_contract_rejects_runner_host_publication(tmp_path: Path) -> None:
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    compose["services"]["team-office-cio"]["ports"] = ["127.0.0.1:9123:8000"]
    path = tmp_path / "compose.yml"
    path.write_text(yaml.safe_dump(compose, sort_keys=False), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--compose", str(path), "--json"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert any("must not publish host ports" in error for error in report["errors"])


def test_network_boundary_contract_rejects_public_worker_network(tmp_path: Path) -> None:
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    compose["networks"]["workers"]["internal"] = False
    path = tmp_path / "compose.yml"
    path.write_text(yaml.safe_dump(compose, sort_keys=False), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--compose", str(path), "--json"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert any("workers' internal flag" in error for error in report["errors"])


def test_network_boundary_contract_rejects_malformed_policy(tmp_path: Path) -> None:
    policy = tmp_path / "network-policy.yaml"
    policy.write_text(
        "schema_version: aiat.network-boundary-policy.v1\n"
        "programme_scope: personal-internal-only\n"
        "runner: {}\n"
        "protected_services: []\n"
        "allowed_gateways: []\n"
        "external_denied_targets: []\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--policy",
            str(policy),
            "--json",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert report["status"] == "fail"
    assert "network policy" in report["errors"][0]


def test_live_probe_classifies_unavailable_engine_as_blocked(monkeypatch) -> None:
    boundary = _load_boundary_module()
    monkeypatch.setattr(boundary.shutil, "which", lambda _name: "/usr/bin/docker")
    monkeypatch.setattr(boundary, "_docker_engine_available", lambda: False)

    report = boundary.inspect_live({"team_services": []})

    assert report["status"] == "blocked"
    assert report["errors"] == ["Docker Engine is unavailable to the Docker CLI"]
