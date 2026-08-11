"""Validate the AIAT team-runner network and credential boundary.

The default mode is a deterministic Compose contract check suitable for CI.
``--live`` extends it with non-secret Docker inspection and TCP probes from
each running team container.  A live run returns exit code 2 when Docker is
unavailable so the release ledger can distinguish an externally blocked check
from a failed boundary.

The live probe intentionally records only names, ports, and pass/fail status;
it never prints container environment values or API credentials.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

MAS_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_COMPOSE = MAS_ROOT / "infra" / "compose" / "docker-compose.yml"
TEAM_PREFIX = "team-"
CEO_SERVICE = "team-exec-ceo"
PROTECTED_SERVICES = (
    "redis",
    "postgres",
    "pgbouncer",
    "minio",
    "opencode-runtime",
)
ALLOWED_GATEWAYS = (
    "message-router",
    "tool-service",
    "orchestrator-api",
    "litellm",
)
FORBIDDEN_ENV_NAMES = frozenset(
    {
        "MAS_API_KEY",
        "PGBOUNCER_DSN",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
    }
)
IDENTITY_ENV_NAMES = frozenset({"AIAT_CEO_API_KEY", "AIAT_WORKER_API_KEY"})


def _service_networks(service: dict[str, Any]) -> set[str]:
    networks = service.get("networks") or []
    if isinstance(networks, dict):
        return {str(name) for name in networks}
    if isinstance(networks, list):
        return {str(name) for name in networks}
    return set()


def _environment_names(service: dict[str, Any]) -> set[str]:
    environment = service.get("environment") or {}
    if isinstance(environment, dict):
        return {str(name) for name in environment}
    names: set[str] = set()
    if isinstance(environment, list):
        for entry in environment:
            if isinstance(entry, str):
                names.add(entry.split("=", 1)[0])
    return names


def _team_services(services: dict[str, Any]) -> list[str]:
    return sorted(
        name
        for name, service in services.items()
        if name.startswith(TEAM_PREFIX) and isinstance(service, dict)
    )


def inspect_static(compose_path: Path = DEFAULT_COMPOSE) -> dict[str, Any]:
    """Return a deterministic static boundary report without contacting Docker."""
    errors: list[str] = []
    warnings: list[str] = []
    try:
        compose = yaml.safe_load(compose_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        return {
            "mode": "static",
            "status": "fail",
            "errors": [f"compose could not be loaded: {exc}"],
            "warnings": [],
            "compose": str(compose_path),
        }

    services = compose.get("services") or {}
    if not isinstance(services, dict):
        errors.append("Compose services must be a mapping")
        services = {}

    defaults = compose.get("x-team-defaults") or {}
    if _service_networks(defaults) != {"workers"}:
        errors.append("x-team-defaults must attach runners only to the workers network")

    team_services = _team_services(services)
    if not team_services:
        errors.append("Compose defines no team-runner services")
    team_environment: dict[str, dict[str, Any]] = {}
    for service_name in team_services:
        service = services[service_name]
        networks = _service_networks(service)
        if networks != {"workers"}:
            errors.append(
                f"{service_name}: runner networks must be exactly ['workers'], got {sorted(networks)}"
            )
        names = _environment_names(service)
        team_environment[service_name] = {
            "identity": sorted(names & IDENTITY_ENV_NAMES),
            "forbidden": sorted(
                name
                for name in names
                if name in FORBIDDEN_ENV_NAMES or name.startswith("MINIO_")
            ),
        }
        expected_identity = (
            "AIAT_CEO_API_KEY" if service_name == CEO_SERVICE else "AIAT_WORKER_API_KEY"
        )
        actual_identity = names & IDENTITY_ENV_NAMES
        if actual_identity != {expected_identity}:
            errors.append(
                f"{service_name}: expected only {expected_identity}, got {sorted(actual_identity)}"
            )
        forbidden = names & FORBIDDEN_ENV_NAMES
        forbidden |= {name for name in names if name.startswith("MINIO_")}
        if forbidden:
            errors.append(f"{service_name}: forbidden data-plane env names {sorted(forbidden)}")
        for volume in service.get("volumes") or []:
            if isinstance(volume, str) and "/var/run/docker.sock" in volume:
                errors.append(f"{service_name}: Docker socket mount is forbidden")

    for service_name in PROTECTED_SERVICES:
        service = services.get(service_name)
        if isinstance(service, dict) and "workers" in _service_networks(service):
            errors.append(f"{service_name}: protected service must not join workers")

    for service_name in ALLOWED_GATEWAYS:
        service = services.get(service_name)
        if not isinstance(service, dict):
            errors.append(f"{service_name}: required runner gateway is missing")
        elif "workers" not in _service_networks(service):
            errors.append(f"{service_name}: required runner gateway is not on workers")

    present_protected = [
        service_name
        for service_name in PROTECTED_SERVICES
        if isinstance(services.get(service_name), dict)
    ]
    identity_services = sorted(
        service_name
        for service_name, service in services.items()
        if isinstance(service, dict)
        and "identity" in service_name.lower()
        and ("postgres" in service_name.lower() or "database" in service_name.lower() or service_name.lower().endswith("-db"))
    )
    missing_optional = [
        service_name
        for service_name in ("identity-postgres", "identity-db")
        if service_name not in services
    ]
    if missing_optional:
        warnings.append(
            "optional identity database services are not in the base Compose profile: "
            + ", ".join(missing_optional)
        )

    return {
        "mode": "static",
        "status": "fail" if errors else "pass",
        "errors": errors,
        "warnings": warnings,
        "compose": str(compose_path),
        "team_services": team_services,
        "team_environment": team_environment,
        "protected_services": present_protected,
        "identity_services": identity_services,
        "allowed_gateways": list(ALLOWED_GATEWAYS),
    }


def _run(command: list[str], *, check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=check,
        capture_output=True,
        text=True,
    )


def _docker_engine_available() -> bool:
    """Return whether the Docker CLI can reach a running Engine.

    A client binary can be present in WSL while the Desktop/Engine socket is
    unavailable.  That condition is an external evidence blocker, not a
    failed boundary assertion, so live mode reports it as ``blocked``.
    """
    result = _run(["docker", "info", "--format", "{{.ServerVersion}}"])
    return result.returncode == 0 and bool(result.stdout.strip())


def _docker_container_for_service(service_name: str) -> str | None:
    result = _run(
        [
            "docker",
            "ps",
            "--filter",
            f"label=com.docker.compose.service={service_name}",
            "--format",
            "{{.ID}}",
        ]
    )
    if result.returncode != 0:
        raise RuntimeError("docker ps failed")
    container_ids = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return container_ids[0] if container_ids else None


def _inspect_networks(container_id: str) -> set[str]:
    result = _run(
        ["docker", "inspect", "--format", "{{json .NetworkSettings.Networks}}", container_id]
    )
    if result.returncode != 0:
        raise RuntimeError("docker inspect failed")
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("docker inspect returned invalid network JSON") from exc
    return set(value) if isinstance(value, dict) else set()


def _logical_network_name(network_name: str) -> str:
    """Resolve a Compose network's logical key without trusting its project prefix.

    Docker names a Compose network ``<project>_<key>`` by default, while the
    static contract uses the logical key (``workers``/``internal``). The
    Compose network label is authoritative; the raw name is retained as a
    fallback for non-Compose test fixtures.
    """

    result = _run(
        [
            "docker",
            "network",
            "inspect",
            network_name,
            "--format",
            '{{index .Labels "com.docker.compose.network"}}',
        ]
    )
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    return network_name


def _logical_networks(container_id: str) -> tuple[set[str], set[str]]:
    """Return ``(logical, actual)`` network names for a container."""

    actual = _inspect_networks(container_id)
    return {_logical_network_name(name) for name in actual}, actual


def _inspect_environment_names(container_id: str) -> set[str]:
    result = _run(["docker", "exec", container_id, "env"])
    if result.returncode != 0:
        raise RuntimeError("docker exec env failed")
    return {
        line.split("=", 1)[0]
        for line in result.stdout.splitlines()
        if line and "=" in line
    }


_TCP_PROBE = (
    "import socket,sys; "
    "h=sys.argv[1]; p=int(sys.argv[2]); "
    "s=socket.create_connection((h,p),timeout=2); s.close()"
)

_FILE_PROBE = "import os,sys; sys.exit(0 if os.path.exists(sys.argv[1]) else 1)"


def _probe_tcp(container_id: str, host: str, port: int) -> bool:
    for interpreter in ("python", "python3"):
        result = _run(
            ["docker", "exec", container_id, interpreter, "-c", _TCP_PROBE, host, str(port)]
        )
        if result.returncode == 0:
            return True
        if "executable file not found" not in (result.stderr or "").lower():
            return False
    raise RuntimeError("team-runner image has neither python nor python3 for probes")


def _probe_storage_health(container_id: str, team_id: str) -> bool:
    code = (
        "import json,os,sys,urllib.request; "
        "u=os.environ['ORCHESTRATOR_URL'].rstrip('/')+'/internal/team-runners/'"
        "+sys.argv[1]+'/storage'; "
        "r=urllib.request.Request(u,data=json.dumps({'operation':'storage_health','payload':{}}).encode(),"
        "headers={'Content-Type':'application/json','X-API-Key':os.environ.get('AIAT_CEO_API_KEY') or os.environ['AIAT_WORKER_API_KEY'],"
        "'X-AIAT-Team-ID':sys.argv[1]},method='POST'); "
        "x=urllib.request.urlopen(r,timeout=5); sys.exit(0 if x.status==200 else 1)"
    )
    for interpreter in ("python", "python3"):
        result = _run(
            ["docker", "exec", container_id, interpreter, "-c", code, team_id]
        )
        if result.returncode == 0:
            return True
        if "executable file not found" not in (result.stderr or "").lower():
            return False
    raise RuntimeError("team-runner image has neither python nor python3 for HTTP probe")


def _probe_file(container_id: str, path: str) -> bool:
    for interpreter in ("python", "python3"):
        result = _run(
            ["docker", "exec", container_id, interpreter, "-c", _FILE_PROBE, path]
        )
        if result.returncode == 0:
            return True
        if "executable file not found" not in (result.stderr or "").lower():
            return False
    raise RuntimeError("team-runner image has neither python nor python3 for file probes")


def inspect_live(
    static_report: dict[str, Any],
    *,
    denied_external: tuple[str, int] = ("1.1.1.1", 443),
) -> dict[str, Any]:
    """Inspect running containers without returning secret values."""
    if shutil.which("docker") is None:
        return {
            "mode": "live",
            "status": "blocked",
            "errors": ["docker CLI is unavailable"],
            "warnings": [],
            "containers": [],
        }
    if not _docker_engine_available():
        return {
            "mode": "live",
            "status": "blocked",
            "errors": ["Docker Engine is unavailable to the Docker CLI"],
            "warnings": [],
            "containers": [],
        }

    errors: list[str] = []
    containers: list[dict[str, Any]] = []
    denied_targets = [
        (name, 6379 if name == "redis" else 5432 if name in {"postgres", "pgbouncer"} else 9000 if name == "minio" else 4096)
        for name in PROTECTED_SERVICES
    ]
    denied_targets.extend(
        (str(name), 5432) for name in static_report.get("identity_services", [])
    )
    denied_targets.append(denied_external)
    allowed_targets = [
        ("message-router", 8001),
        ("tool-service", 8002),
        ("orchestrator-api", 8000),
        ("litellm", 4000),
    ]
    for service_name in static_report.get("team_services", []):
        entry: dict[str, Any] = {"service": service_name}
        try:
            container_id = _docker_container_for_service(service_name)
            if not container_id:
                errors.append(f"{service_name}: no running container found")
                entry["status"] = "missing"
                containers.append(entry)
                continue
            entry["container_id"] = container_id[:12]
            networks, actual_networks = _logical_networks(container_id)
            entry["networks"] = sorted(actual_networks)
            entry["logical_networks"] = sorted(networks)
            if networks != {"workers"}:
                errors.append(f"{service_name}: live logical networks are {sorted(networks)}")
            names = _inspect_environment_names(container_id)
            entry["forbidden_env"] = sorted(
                name
                for name in names
                if name in FORBIDDEN_ENV_NAMES or name.startswith("MINIO_")
            )
            if entry["forbidden_env"]:
                errors.append(f"{service_name}: live forbidden env names present")
            socket_present = _probe_file(container_id, "/var/run/docker.sock")
            entry["docker_socket_present"] = socket_present
            if socket_present:
                errors.append(f"{service_name}: Docker socket is mounted")
            expected_identity = (
                "AIAT_CEO_API_KEY" if service_name == CEO_SERVICE else "AIAT_WORKER_API_KEY"
            )
            if (names & IDENTITY_ENV_NAMES) != {expected_identity}:
                errors.append(f"{service_name}: live identity env mismatch")

            denied_results: list[dict[str, Any]] = []
            for host, port in denied_targets:
                allowed = _probe_tcp(container_id, host, port)
                denied_results.append({"host": host, "port": port, "denied": not allowed})
                if allowed:
                    errors.append(f"{service_name}: direct connection allowed to {host}:{port}")
            allowed_results: list[dict[str, Any]] = []
            for host, port in allowed_targets:
                reachable = _probe_tcp(container_id, host, port)
                allowed_results.append({"host": host, "port": port, "reachable": reachable})
                if not reachable:
                    errors.append(f"{service_name}: gateway {host}:{port} is unreachable")
            team_id = service_name.removeprefix(TEAM_PREFIX).replace("-", "_")
            storage_ok = _probe_storage_health(container_id, team_id)
            entry["storage_health"] = storage_ok
            if not storage_ok:
                errors.append(f"{service_name}: control-plane storage health failed")
            entry["denied"] = denied_results
            entry["allowed"] = allowed_results
            entry["status"] = "pass"
        except (RuntimeError, OSError) as exc:
            entry["status"] = "error"
            errors.append(f"{service_name}: live probe failed: {exc}")
        containers.append(entry)

    return {
        "mode": "live",
        "status": "fail" if errors else "pass",
        "errors": errors,
        "warnings": [],
        "containers": containers,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compose", type=Path, default=DEFAULT_COMPOSE)
    parser.add_argument("--live", action="store_true", help="probe running team containers")
    parser.add_argument("--json", action="store_true", help="emit one JSON report")
    args = parser.parse_args(argv)

    report = inspect_static(args.compose)
    if args.live and report["status"] == "pass":
        report = {
            **report,
            "live": inspect_live(report),
        }
        status = report["live"]["status"]
        exit_code = 2 if status == "blocked" else 1 if status != "pass" else 0
    else:
        exit_code = 1 if report["status"] != "pass" else 0
        if args.live:
            report = {**report, "live": {"mode": "live", "status": "not-run"}}

    if args.json:
        print(json.dumps({"generated_at": datetime.now(tz=UTC).isoformat(), **report}, sort_keys=True))
    else:
        print(f"network-boundary: {report['status']} ({'live' if args.live else 'static'})")
        for error in report.get("errors", []):
            print(f"network-boundary: {error}", file=sys.stderr)
        if args.live:
            live = report.get("live") or {}
            print(f"network-boundary: live={live.get('status')}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
