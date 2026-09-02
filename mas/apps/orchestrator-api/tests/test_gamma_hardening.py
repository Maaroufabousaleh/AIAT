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
        "litellm",
        "orchestrator-api",
    }
    assert all("workers" in compose["services"][name]["networks"] for name in required)
    assert "workers" not in compose["services"]["redis"]["networks"]
    assert "workers" not in compose["services"]["postgres"]["networks"]
    assert "workers" not in compose["services"]["pgbouncer"]["networks"]
    assert "workers" not in compose["services"]["minio"]["networks"]
    assert "workers" not in compose["services"]["opencode-runtime"]["networks"]

    team_env = compose["x-team-env"]
    assert "PGBOUNCER_DSN" not in team_env
    assert all(not key.startswith("MINIO_") for key in team_env)
    assert "MAS_API_KEY" not in team_env
    assert "AIAT_CEO_API_KEY" not in team_env
    team_services = [
        name for name in compose["services"] if name.startswith("team-")
    ]
    assert team_services
    assert all("MAS_API_KEY" not in compose["services"][name]["environment"] for name in team_services)
    assert "AIAT_CEO_API_KEY" in compose["services"]["team-exec-ceo"]["environment"]
    assert "AIAT_WORKER_API_KEY" not in compose["services"]["team-exec-ceo"]["environment"]
    assert all(
        "AIAT_CEO_API_KEY" not in compose["services"][name]["environment"]
        for name in team_services
        if name != "team-exec-ceo"
    )


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
        "browser_profiles:/var/lib/aiat/browser-profiles",
    ]


def test_opencode_workspace_is_initialized_for_the_runtime_user() -> None:
    compose_path = Path(__file__).resolve().parents[3] / "infra" / "compose" / "docker-compose.yml"
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))

    initializer = compose["services"]["opencode-workspace-init"]
    assert initializer["user"] == "0:0"
    assert initializer["network_mode"] == "none"
    assert initializer["volumes"] == [
        "opencode_workspace:/workspace",
        "browser_profiles:/browser-profiles",
    ]
    assert (
        "chown -R 10001:10001 /workspace /browser-profiles"
        in initializer["command"][-1]
    )
    for service_name in ("tool-service", "opencode-runtime"):
        dependency = compose["services"][service_name]["depends_on"]["opencode-workspace-init"]
        assert dependency == {"condition": "service_completed_successfully"}


def test_prometheus_uses_orchestrator_api_secret() -> None:
    compose_dir = Path(__file__).resolve().parents[3] / "infra" / "compose"
    dev_compose = yaml.safe_load((compose_dir / "docker-compose.dev.yml").read_text(encoding="utf-8"))
    prometheus = yaml.safe_load((compose_dir / "prometheus.yml").read_text(encoding="utf-8"))

    assert dev_compose["services"]["prometheus"]["secrets"] == ["orchestrator_api_key"]
    assert dev_compose["secrets"]["orchestrator_api_key"]["environment"] == "MAS_API_KEY"
    job = next(job for job in prometheus["scrape_configs"] if job["job_name"] == "orchestrator-api")
    assert job["authorization"]["credentials_file"] == "/run/secrets/orchestrator_api_key"
