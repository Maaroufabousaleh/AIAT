from pathlib import Path

import yaml


def test_compose_does_not_mount_the_docker_socket() -> None:
    compose_path = Path(__file__).resolve().parents[3] / "infra" / "compose" / "docker-compose.yml"
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))

    socket_mounts: list[tuple[str, str]] = []
    for service_name, service in compose["services"].items():
        for volume in service.get("volumes", []) or []:
            if isinstance(volume, str) and "/var/run/docker.sock" in volume:
                socket_mounts.append((service_name, volume))

    assert socket_mounts == []


def test_worker_network_has_only_required_service_gateways() -> None:
    compose_path = Path(__file__).resolve().parents[3] / "infra" / "compose" / "docker-compose.yml"
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))

    assert compose["x-team-defaults"]["networks"] == ["workers"]
    required = {
        "message-router",
        "tool-service",
        "pgbouncer",
        "minio",
        "litellm",
        "orchestrator-api",
    }
    assert all("workers" in compose["services"][name]["networks"] for name in required)
    assert "workers" not in compose["services"]["redis"]["networks"]
    assert "workers" not in compose["services"]["postgres"]["networks"]


def test_egress_and_scoped_workspace_are_preserved() -> None:
    compose_path = Path(__file__).resolve().parents[3] / "infra" / "compose" / "docker-compose.yml"
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))

    assert "public" in compose["services"]["tool-service"]["networks"]
    assert "public" in compose["services"]["orchestrator-api"]["networks"]
    tool = compose["services"]["tool-service"]
    assert tool["environment"]["TOOL_WORKSPACE_ROOT"] == "/workspace"
    assert tool["volumes"] == [
        "${AIAT_WORKSPACE_ROOT:-../../..}:/workspace",
        "opencode_workspace:/opencode-workspace",
    ]


def test_prometheus_uses_orchestrator_api_secret() -> None:
    compose_dir = Path(__file__).resolve().parents[3] / "infra" / "compose"
    dev_compose = yaml.safe_load((compose_dir / "docker-compose.dev.yml").read_text(encoding="utf-8"))
    prometheus = yaml.safe_load((compose_dir / "prometheus.yml").read_text(encoding="utf-8"))

    assert dev_compose["services"]["prometheus"]["secrets"] == ["orchestrator_api_key"]
    assert dev_compose["secrets"]["orchestrator_api_key"]["environment"] == "MAS_API_KEY"
    job = next(job for job in prometheus["scrape_configs"] if job["job_name"] == "orchestrator-api")
    assert job["authorization"]["credentials_file"] == "/run/secrets/orchestrator_api_key"
