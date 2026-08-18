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
DEFAULT_POLICY = MAS_ROOT / "docs" / "provenance" / "network_boundary_policy.yaml"
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


def _load_policy(policy_path: Path = DEFAULT_POLICY) -> dict[str, Any]:
    """Load and validate the checked-in deny/allow matrix policy."""
    try:
        raw = yaml.safe_load(policy_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"network policy could not be loaded: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("network policy must be a mapping")
    if raw.get("schema_version") != "aiat.network-boundary-policy.v1":
        raise ValueError("network policy has an unsupported schema_version")
    if raw.get("programme_scope") != "personal-internal-only":
        raise ValueError("network policy must declare personal-internal-only scope")
    runner = raw.get("runner")
    protected = raw.get("protected_services")
    allowed = raw.get("allowed_gateways")
    external = raw.get("external_denied_targets")
    networks = raw.get("networks")
    if not isinstance(runner, dict) or not isinstance(protected, list) or not isinstance(allowed, list) or not isinstance(external, list) or not isinstance(networks, dict):
        raise ValueError("network policy runner, protected, allowed, and external sections are required")
    if not protected or not allowed or not external:
        raise ValueError("network policy protected, allowed, and external sections must not be empty")
    required_runner = {"service_prefix", "ceo_service", "network", "identity_env", "forbidden_env_names", "forbidden_env_prefixes", "forbidden_mounts"}
    if not required_runner.issubset(runner):
        missing = sorted(required_runner - set(runner))
        raise ValueError(f"network policy runner fields missing: {missing}")
    for section_name, rows in (("protected_services", protected), ("allowed_gateways", allowed)):
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("service"), str) or not isinstance(row.get("network"), str) or not isinstance(row.get("port"), int):
                raise ValueError(f"network policy {section_name} rows must contain service, network, and integer port")
            service = row["service"]
            if service in seen:
                raise ValueError(f"network policy {section_name} contains duplicate service {service!r}")
            seen.add(service)
            if not 1 <= row["port"] <= 65535:
                raise ValueError(f"network policy {service} has an invalid port")
    for row in external:
        if not isinstance(row, dict) or not isinstance(row.get("host"), str) or not isinstance(row.get("port"), int):
            raise ValueError("network policy external targets must contain host and integer port")
        if not 1 <= row["port"] <= 65535:
            raise ValueError("network policy external target has an invalid port")
    identity_env = runner.get("identity_env")
    if not isinstance(identity_env, dict) or set(identity_env) != {"ceo", "worker"} or not all(isinstance(value, str) and value for value in identity_env.values()):
        raise ValueError("network policy identity_env must define non-empty ceo and worker names")
    for field in ("forbidden_env_names", "forbidden_env_prefixes", "forbidden_mounts"):
        values = runner.get(field)
        if not isinstance(values, list) or not all(isinstance(value, str) and value for value in values):
            raise ValueError(f"network policy runner.{field} must be a list of non-empty strings")
    for network_name, network_config in networks.items():
        if not isinstance(network_name, str) or not network_name or not isinstance(network_config, dict) or not isinstance(network_config.get("internal"), bool):
            raise ValueError("network policy networks must map names to an internal boolean")
    identity_defaults = raw.get("identity_service_defaults") or {}
    if not isinstance(identity_defaults, dict) or not isinstance(identity_defaults.get("port"), int) or not 1 <= identity_defaults["port"] <= 65535:
        raise ValueError("network policy identity_service_defaults.port must be a valid integer port")
    return raw


def _policy_targets(policy: dict[str, Any]) -> dict[str, Any]:
    runner = policy["runner"]
    protected = policy["protected_services"]
    allowed = policy["allowed_gateways"]
    return {
        "schema_version": policy["schema_version"],
        "runner_network": runner["network"],
        "service_prefix": runner["service_prefix"],
        "ceo_service": runner["ceo_service"],
        "identity_env": dict(runner["identity_env"]),
        "forbidden_env_names": list(runner["forbidden_env_names"]),
        "forbidden_env_prefixes": list(runner["forbidden_env_prefixes"]),
        "forbidden_mounts": list(runner["forbidden_mounts"]),
        "networks": {
            str(name): {"internal": bool(config["internal"])}
            for name, config in policy["networks"].items()
        },
        "protected": [
            {"service": row["service"], "network": row["network"], "port": row["port"]}
            for row in protected
        ],
        "allowed": [
            {"service": row["service"], "network": row["network"], "port": row["port"]}
            for row in allowed
        ],
        "external_denied": [
            {"host": row["host"], "port": row["port"]}
            for row in policy["external_denied_targets"]
        ],
        "identity_service_port": policy["identity_service_defaults"]["port"],
    }


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


def inspect_static(
    compose_path: Path = DEFAULT_COMPOSE,
    *,
    policy_path: Path = DEFAULT_POLICY,
) -> dict[str, Any]:
    """Return a deterministic static boundary report without contacting Docker."""
    errors: list[str] = []
    warnings: list[str] = []
    try:
        policy = _load_policy(policy_path)
        targets = _policy_targets(policy)
    except ValueError as exc:
        return {
            "mode": "static",
            "status": "fail",
            "errors": [str(exc)],
            "warnings": [],
            "compose": str(compose_path),
            "policy": str(policy_path),
        }
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

    runner_network = str(targets["runner_network"])
    service_prefix = str(targets["service_prefix"])
    ceo_service = str(targets["ceo_service"])
    defaults = compose.get("x-team-defaults") or {}
    if _service_networks(defaults) != {runner_network}:
        errors.append(
            f"x-team-defaults must attach runners only to the {runner_network} network"
        )
    compose_networks = compose.get("networks") or {}
    for network_name, network_config in targets["networks"].items():
        actual = compose_networks.get(network_name)
        if not isinstance(actual, dict):
            errors.append(f"Compose network {network_name!r} is missing")
            continue
        expected_internal = bool(network_config["internal"])
        if bool(actual.get("internal", False)) != expected_internal:
            errors.append(
                f"Compose network {network_name!r} internal flag must be {expected_internal}"
            )

    team_services = sorted(
        name
        for name, service in services.items()
        if name.startswith(service_prefix) and isinstance(service, dict)
    )
    if not team_services:
        errors.append("Compose defines no team-runner services")
    team_environment: dict[str, dict[str, Any]] = {}
    for service_name in team_services:
        service = services[service_name]
        networks = _service_networks(service)
        if networks != {runner_network}:
            errors.append(
                f"{service_name}: runner networks must be exactly [{runner_network!r}], got {sorted(networks)}"
            )
        if service.get("ports"):
            errors.append(f"{service_name}: runner must not publish host ports")
        names = _environment_names(service)
        identity_env = targets["identity_env"]
        team_environment[service_name] = {
            "identity": sorted(
                names & {str(identity_env["ceo"]), str(identity_env["worker"])}
            ),
            "forbidden": sorted(
                name
                for name in names
                if name in set(targets["forbidden_env_names"])
                or any(name.startswith(prefix) for prefix in targets["forbidden_env_prefixes"])
            ),
        }
        expected_identity = (
            str(identity_env["ceo"])
            if service_name == ceo_service
            else str(identity_env["worker"])
        )
        actual_identity = names & {str(identity_env["ceo"]), str(identity_env["worker"])}
        if actual_identity != {expected_identity}:
            errors.append(
                f"{service_name}: expected only {expected_identity}, got {sorted(actual_identity)}"
            )
        forbidden = names & set(targets["forbidden_env_names"])
        forbidden |= {
            name
            for name in names
            if any(name.startswith(prefix) for prefix in targets["forbidden_env_prefixes"])
        }
        if forbidden:
            errors.append(f"{service_name}: forbidden data-plane env names {sorted(forbidden)}")
        for volume in service.get("volumes") or []:
            if isinstance(volume, str) and any(
                mount in volume for mount in targets["forbidden_mounts"]
            ):
                errors.append(f"{service_name}: forbidden host-control mount is present")

    protected_rows = targets["protected"]
    protected_services = [str(row["service"]) for row in protected_rows]
    for row in protected_rows:
        service_name = str(row["service"])
        service = services.get(service_name)
        if not isinstance(service, dict):
            errors.append(f"{service_name}: protected service is missing")
            continue
        expected_network = str(row["network"])
        if _service_networks(service) != {expected_network}:
            errors.append(
                f"{service_name}: protected service networks must be exactly [{expected_network!r}], got {sorted(_service_networks(service))}"
            )
        if service.get("ports"):
            errors.append(f"{service_name}: protected service must not publish host ports")

    allowed_rows = targets["allowed"]
    for row in allowed_rows:
        service_name = str(row["service"])
        service = services.get(service_name)
        if not isinstance(service, dict):
            errors.append(f"{service_name}: required runner gateway is missing")
        elif str(row["network"]) not in _service_networks(service):
            errors.append(
                f"{service_name}: required runner gateway is not on {row['network']}"
            )

    present_protected = [
        service_name
        for service_name in protected_services
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
        "policy": str(policy_path),
        "boundary_policy": targets,
        "team_services": team_services,
        "team_environment": team_environment,
        "protected_services": present_protected,
        "identity_services": identity_services,
        "allowed_gateways": [str(row["service"]) for row in allowed_rows],
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
    denied_external: tuple[str, int] | None = None,
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
    policy = static_report.get("boundary_policy") or {}
    protected_rows = policy.get("protected") or []
    allowed_rows = policy.get("allowed") or []
    denied_targets = [
        (str(row["service"]), int(row["port"]))
        for row in protected_rows
        if isinstance(row, dict) and "service" in row and "port" in row
    ]
    denied_targets.extend(
        (str(name), int(policy.get("identity_service_port", 5432)))
        for name in static_report.get("identity_services", [])
    )
    external_targets = policy.get("external_denied") or []
    denied_targets.extend(
        (str(row["host"]), int(row["port"]))
        for row in external_targets
        if isinstance(row, dict) and "host" in row and "port" in row
    )
    if denied_external is not None and denied_external not in denied_targets:
        denied_targets.append(denied_external)
    allowed_targets = [
        (str(row["service"]), int(row["port"]))
        for row in allowed_rows
        if isinstance(row, dict) and "service" in row and "port" in row
    ]
    identity_env = policy.get("identity_env") or {
        "ceo": "AIAT_CEO_API_KEY",
        "worker": "AIAT_WORKER_API_KEY",
    }
    forbidden_env_names = set(policy.get("forbidden_env_names") or FORBIDDEN_ENV_NAMES)
    forbidden_env_prefixes = tuple(policy.get("forbidden_env_prefixes") or ("MINIO_",))
    runner_network = str(policy.get("runner_network") or "workers")
    ceo_service = str(policy.get("ceo_service") or CEO_SERVICE)
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
            if networks != {runner_network}:
                errors.append(f"{service_name}: live logical networks are {sorted(networks)}")
            names = _inspect_environment_names(container_id)
            entry["forbidden_env"] = sorted(
                name
                for name in names
                if name in forbidden_env_names
                or any(name.startswith(prefix) for prefix in forbidden_env_prefixes)
            )
            if entry["forbidden_env"]:
                errors.append(f"{service_name}: live forbidden env names present")
            socket_present = _probe_file(container_id, "/var/run/docker.sock")
            entry["docker_socket_present"] = socket_present
            if socket_present:
                errors.append(f"{service_name}: Docker socket is mounted")
            expected_identity = (
                str(identity_env["ceo"])
                if service_name == ceo_service
                else str(identity_env["worker"])
            )
            if (names & {str(identity_env["ceo"]), str(identity_env["worker"])} ) != {expected_identity}:
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
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--live", action="store_true", help="probe running team containers")
    parser.add_argument("--json", action="store_true", help="emit one JSON report")
    args = parser.parse_args(argv)

    report = inspect_static(args.compose, policy_path=args.policy)
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
