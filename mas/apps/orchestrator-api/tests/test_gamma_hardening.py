from pathlib import Path

import yaml


def test_compose_docker_socket_exposure_is_dashboard_read_only() -> None:
    compose_path = Path(__file__).resolve().parents[3] / "infra" / "compose" / "docker-compose.yml"
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))

    socket_mounts: list[tuple[str, str]] = []
    for service_name, service in compose["services"].items():
        for volume in service.get("volumes", []) or []:
            if isinstance(volume, str) and "/var/run/docker.sock" in volume:
                socket_mounts.append((service_name, volume))

    assert socket_mounts == [("dashboard", "/var/run/docker.sock:/var/run/docker.sock:ro")]
