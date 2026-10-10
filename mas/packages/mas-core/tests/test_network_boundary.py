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
        "network": "sandbox_gateway",
        "port": 4096,
    }
    assert report["boundary_policy"]["allowed"][0] == {
        "service": "message-router",
        "network": "workers",
        "port": 8001,
    }
    assert report["boundary_policy"]["control_plane"] == {
        "service": "orchestrator-api",
        "network": "internal",
        "required_tcp": [
            {"service": "redis", "port": 6379},
            {"service": "postgres", "port": 5432},
            {"service": "pgbouncer", "port": 5432},
            {"service": "tool-service", "port": 8002},
        ],
    }
    assert report["boundary_policy"]["networks"] == {
        "internal": {"internal": True},
        "sandbox_gateway": {"internal": True},
        "workers": {"internal": True},
    }
    assert report["runsc_network"] == {
        "service": "opencode-runtime",
        "network": "sandbox_gateway",
        "subnet": "172.31.250.0/29",
        "static_ipv4": {
            "opencode-runtime": "172.31.250.2",
            "tool-service": "172.31.250.3",
            "litellm": "172.31.250.4",
            "orchestrator-api": "172.31.250.5",
        },
        "host_mappings": {
            "tool-service": "172.31.250.3",
            "litellm": "172.31.250.4",
        },
        "network_members": ["litellm", "opencode-runtime", "orchestrator-api", "tool-service"],
        "allowed_gateways": [
            {"service": "tool-service", "port": 8002},
            {"service": "litellm", "port": 4000},
        ],
        "protected_services": ["redis", "postgres", "pgbouncer", "minio"],
    }


def test_network_boundary_contract_rejects_broken_runsc_gateway_mapping(tmp_path: Path) -> None:
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    compose["services"]["opencode-runtime"]["extra_hosts"] = ["tool-service=172.31.250.99"]
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
    assert any("extra_hosts must map tool-service" in error for error in report["errors"])


def test_network_boundary_contract_rejects_protected_service_on_runsc_gateway(tmp_path: Path) -> None:
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    compose["services"]["redis"]["networks"] = ["internal", "sandbox_gateway"]
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
    assert any("redis: protected service must not join sandbox_gateway" in error for error in report["errors"])


def test_network_boundary_contract_rejects_runsc_broad_network_and_control_plane_credentials(tmp_path: Path) -> None:
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    compose["services"]["opencode-runtime"]["networks"] = ["internal", "sandbox_gateway"]
    compose["services"]["opencode-runtime"]["environment"]["MAS_API_KEY"] = "placeholder"
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
    assert any("networks must be exactly ['sandbox_gateway']" in error for error in report["errors"])
    assert any("MAS_API_KEY" in error for error in report["errors"])


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


def test_runsc_live_probe_requires_gateway_reachability_and_protected_denials(monkeypatch) -> None:
    boundary = _load_boundary_module()
    container_ids = {
        "opencode-runtime": "runsc-container",
        "tool-service": "tool-container",
        "litellm": "litellm-container",
        "redis": "redis-container",
        "postgres": "postgres-container",
        "pgbouncer": "pgbouncer-container",
        "minio": "minio-container",
    }
    addresses = {
        ("tool-container", "sandbox_gateway"): "172.31.250.3",
        ("litellm-container", "sandbox_gateway"): "172.31.250.4",
        ("redis-container", "internal"): "172.20.0.2",
        ("postgres-container", "internal"): "172.20.0.3",
        ("pgbouncer-container", "internal"): "172.20.0.4",
        ("minio-container", "internal"): "172.20.0.5",
    }
    monkeypatch.setattr(
        boundary,
        "_docker_container_for_service",
        lambda service: container_ids.get(service),
    )
    monkeypatch.setattr(
        boundary,
        "_run",
        lambda command: subprocess.CompletedProcess(command, 0, stdout="runsc\n", stderr=""),
    )
    monkeypatch.setattr(
        boundary,
        "_logical_networks",
        lambda _container: ({"sandbox_gateway"}, {"mas_sandbox_gateway"}),
    )
    monkeypatch.setattr(
        boundary,
        "_container_ipv4",
        lambda container, network: addresses.get((container, network)),
    )

    def probe(_container: str, host: str, _port: int) -> dict[str, object]:
        if host in {"tool-service", "litellm"}:
            return {
                "reachable": True,
                "resolved_addresses": [addresses[(container_ids[host], "sandbox_gateway")]],
                "reason": None,
            }
        return {
            "reachable": False,
            "resolved_addresses": [host],
            "reason": "ENETUNREACH",
        }

    monkeypatch.setattr(boundary, "_probe_runsc_tcp", probe)
    protected_rows = [
        {"service": "redis", "port": 6379},
        {"service": "postgres", "port": 5432},
        {"service": "pgbouncer", "port": 5432},
        {"service": "minio", "port": 9000},
    ]

    report = boundary._inspect_runsc_boundary(protected_rows)

    assert report["status"] == "pass"
    assert report["runtime"] == "runsc"
    assert all(row["reachable"] for row in report["gateway_probes"])
    assert all(row["denied"] for row in report["protected_denials"])


def test_runsc_live_probe_does_not_count_unreachable_gateways_as_protected_denials(monkeypatch) -> None:
    boundary = _load_boundary_module()
    monkeypatch.setattr(
        boundary,
        "_docker_container_for_service",
        lambda service: f"{service}-container",
    )
    monkeypatch.setattr(
        boundary,
        "_run",
        lambda command: subprocess.CompletedProcess(command, 0, stdout="runsc\n", stderr=""),
    )
    monkeypatch.setattr(
        boundary,
        "_logical_networks",
        lambda _container: ({"sandbox_gateway"}, {"mas_sandbox_gateway"}),
    )
    monkeypatch.setattr(boundary, "_container_ipv4", lambda _container, _network: "172.31.250.3")
    monkeypatch.setattr(
        boundary,
        "_probe_runsc_tcp",
        lambda _container, _host, _port: {
            "reachable": False,
            "resolved_addresses": [],
            "reason": "EAI_AGAIN",
        },
    )

    report = boundary._inspect_runsc_boundary([])

    assert report["status"] == "fail"
    assert report["protected_denials"]
    assert all(row["denied"] is None for row in report["protected_denials"])
    assert all(row["reason"] == "allowed_gateway_probe_failed" for row in report["protected_denials"])


def test_live_probe_marks_runner_failed_when_its_storage_health_fails(monkeypatch) -> None:
    boundary = _load_boundary_module()
    monkeypatch.setattr(boundary.shutil, "which", lambda _name: "/usr/bin/docker")
    monkeypatch.setattr(boundary, "_docker_engine_available", lambda: True)
    monkeypatch.setattr(boundary, "_docker_container_for_service", lambda _service: "container-id")
    monkeypatch.setattr(
        boundary,
        "_logical_networks",
        lambda _container: ({"workers"}, {"workers"}),
    )
    monkeypatch.setattr(
        boundary,
        "_inspect_environment_names",
        lambda _container: {"AIAT_WORKER_API_KEY"},
    )
    monkeypatch.setattr(boundary, "_probe_file", lambda _container, _path: False)
    monkeypatch.setattr(
        boundary,
        "_probe_tcp",
        lambda _container, host, _port: host == "tool-service",
    )
    monkeypatch.setattr(boundary, "_probe_storage_health", lambda _container, _team_id: False)

    report = boundary.inspect_live(
        {
            "team_services": ["team-office-test"],
            "boundary_policy": {
                "protected": [{"service": "postgres", "port": 5432}],
                "allowed": [{"service": "tool-service", "port": 8002}],
                "external_denied": [{"host": "example.invalid", "port": 443}],
                "identity_env": {"ceo": "AIAT_CEO_API_KEY", "worker": "AIAT_WORKER_API_KEY"},
                "forbidden_env_names": [],
                "forbidden_env_prefixes": [],
                "runner_network": "workers",
                "ceo_service": "team-exec-ceo",
            },
        }
    )

    assert report["status"] == "fail"
    assert report["containers"][0]["storage_health"] is False
    assert report["containers"][0]["status"] == "fail"


def test_live_probe_checks_control_plane_connectivity_without_running_runners(monkeypatch) -> None:
    boundary = _load_boundary_module()
    monkeypatch.setattr(boundary.shutil, "which", lambda _name: "/usr/bin/docker")
    monkeypatch.setattr(boundary, "_docker_engine_available", lambda: True)
    monkeypatch.setattr(
        boundary,
        "_docker_container_for_service",
        lambda service: "control-container" if service == "orchestrator-api" else None,
    )
    monkeypatch.setattr(
        boundary,
        "_logical_networks",
        lambda _container: ({"internal", "workers"}, {"mas_internal", "mas_workers"}),
    )
    monkeypatch.setattr(boundary, "_probe_tcp", lambda _container, _host, _port: True)

    report = boundary.inspect_live(
        {
            "team_services": [],
            "identity_services": [],
            "boundary_policy": {
                "control_plane": {
                    "service": "orchestrator-api",
                    "network": "internal",
                    "required_tcp": [
                        {"service": "redis", "port": 6379},
                        {"service": "tool-service", "port": 8002},
                    ],
                },
                "protected": [],
                "allowed": [],
                "external_denied": [],
            },
        }
    )

    assert report["status"] == "pass"
    assert report["control_plane"]["status"] == "pass"
    assert report["control_plane"]["required_connections"] == [
        {"host": "redis", "port": 6379, "reachable": True},
        {"host": "tool-service", "port": 8002, "reachable": True},
    ]


def test_live_probe_fails_when_control_plane_internal_dependency_times_out(monkeypatch) -> None:
    boundary = _load_boundary_module()
    monkeypatch.setattr(boundary.shutil, "which", lambda _name: "/usr/bin/docker")
    monkeypatch.setattr(boundary, "_docker_engine_available", lambda: True)
    monkeypatch.setattr(boundary, "_docker_container_for_service", lambda _service: "control-container")
    monkeypatch.setattr(
        boundary,
        "_logical_networks",
        lambda _container: ({"internal"}, {"mas_internal"}),
    )
    monkeypatch.setattr(
        boundary,
        "_probe_tcp",
        lambda _container, host, _port: host == "tool-service",
    )

    report = boundary.inspect_live(
        {
            "team_services": [],
            "identity_services": [],
            "boundary_policy": {
                "control_plane": {
                    "service": "orchestrator-api",
                    "network": "internal",
                    "required_tcp": [
                        {"service": "redis", "port": 6379},
                        {"service": "tool-service", "port": 8002},
                    ],
                },
                "protected": [],
                "allowed": [],
                "external_denied": [],
            },
        }
    )

    assert report["status"] == "fail"
    assert report["control_plane"]["status"] == "fail"
    assert any("required internal service redis:6379 is unreachable" in error for error in report["errors"])
