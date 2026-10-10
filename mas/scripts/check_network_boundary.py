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
import ipaddress
import json
import re
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
RUNSC_SERVICE = "opencode-runtime"
RUNSC_NETWORK = "sandbox_gateway"
RUNSC_GATEWAYS = (("tool-service", 8002), ("litellm", 4000))
RUNSC_PROTECTED = ("redis", "postgres", "pgbouncer", "minio")
RUNSC_NETWORK_MEMBERS = frozenset({RUNSC_SERVICE, "orchestrator-api", *(name for name, _port in RUNSC_GATEWAYS)})
RUNSC_FORBIDDEN_ENV = frozenset(
    {
        "MAS_API_KEY",
        "AIAT_OPERATOR_API_KEY",
        "AIAT_CEO_API_KEY",
        "AIAT_WORKER_API_KEY",
        "PGBOUNCER_DSN",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "TOOL_SECRET",
    }
)


def _compose_default(value: Any) -> str:
    """Resolve the literal default in a simple Compose ${NAME:-default} value."""
    raw = str(value)
    match = re.fullmatch(r"\$\{[A-Za-z_][A-Za-z0-9_]*:-([^{}]+)\}", raw)
    return match.group(1) if match else raw


def _network_ipv4(service: dict[str, Any], network: str) -> str | None:
    networks = service.get("networks") or {}
    if not isinstance(networks, dict):
        return None
    config = networks.get(network)
    if not isinstance(config, dict) or "ipv4_address" not in config:
        return None
    return _compose_default(config["ipv4_address"])


def _extra_host_entries(service: dict[str, Any]) -> dict[str, str]:
    entries = service.get("extra_hosts") or []
    if isinstance(entries, dict):
        return {str(host): _compose_default(address) for host, address in entries.items()}
    result: dict[str, str] = {}
    if isinstance(entries, list):
        for entry in entries:
            if isinstance(entry, str):
                separator = "=" if "=" in entry else ":"
                if separator in entry:
                    host, address = entry.split(separator, 1)
                    result[host.strip()] = _compose_default(address.strip())
    return result


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
    control_plane = raw.get("control_plane")
    protected = raw.get("protected_services")
    allowed = raw.get("allowed_gateways")
    external = raw.get("external_denied_targets")
    networks = raw.get("networks")
    if not isinstance(runner, dict) or not isinstance(control_plane, dict) or not isinstance(protected, list) or not isinstance(allowed, list) or not isinstance(external, list) or not isinstance(networks, dict):
        raise ValueError("network policy runner, control_plane, protected, allowed, and external sections are required")
    if not protected or not allowed or not external:
        raise ValueError("network policy protected, allowed, and external sections must not be empty")
    if (
        not isinstance(control_plane.get("service"), str)
        or not control_plane["service"]
        or not isinstance(control_plane.get("network"), str)
        or not control_plane["network"]
        or not isinstance(control_plane.get("required_tcp"), list)
        or not control_plane["required_tcp"]
    ):
        raise ValueError("network policy control_plane requires service, network, and required_tcp rows")
    for row in control_plane["required_tcp"]:
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("service"), str)
            or not row["service"]
            or not isinstance(row.get("port"), int)
            or not 1 <= row["port"] <= 65535
        ):
            raise ValueError("network policy control_plane required_tcp rows must contain a service and valid port")
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
        "control_plane": {
            "service": str(policy["control_plane"]["service"]),
            "network": str(policy["control_plane"]["network"]),
            "required_tcp": [
                {"service": str(row["service"]), "port": int(row["port"])}
                for row in policy["control_plane"]["required_tcp"]
            ],
        },
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
    control_plane = targets["control_plane"]
    control_plane_service = str(control_plane["service"])
    control_plane_network = str(control_plane["network"])
    control_plane_compose = services.get(control_plane_service)
    if not isinstance(control_plane_compose, dict):
        errors.append(f"control-plane service {control_plane_service!r} is missing")
    elif control_plane_network not in _service_networks(control_plane_compose):
        errors.append(
            f"control-plane service {control_plane_service!r} is not attached to {control_plane_network}"
        )
    for row in control_plane["required_tcp"]:
        service_name = str(row["service"])
        service = services.get(service_name)
        if not isinstance(service, dict):
            errors.append(f"control-plane dependency {service_name!r} is missing")
        elif control_plane_network not in _service_networks(service):
            errors.append(
                f"control-plane dependency {service_name!r} is not attached to {control_plane_network}"
            )
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

    # runsc cannot reach Docker's embedded bridge DNS at 127.0.0.11. Require
    # the OpenCode sandbox to use a private bridge with stable /etc/hosts
    # mappings for only the mediated tool and model-gateway services.
    sandbox_network = compose_networks.get(RUNSC_NETWORK)
    sandbox_subnet: ipaddress.IPv4Network | None = None
    sandbox_ipam = sandbox_network.get("ipam") if isinstance(sandbox_network, dict) else None
    ipam_rows = sandbox_ipam.get("config") if isinstance(sandbox_ipam, dict) else None
    subnet_value = None
    if isinstance(ipam_rows, list) and ipam_rows and isinstance(ipam_rows[0], dict):
        subnet_value = _compose_default(ipam_rows[0].get("subnet", ""))
    try:
        parsed_subnet = ipaddress.ip_network(str(subnet_value), strict=True)
        if not isinstance(parsed_subnet, ipaddress.IPv4Network):
            raise ValueError("sandbox gateway subnet must be IPv4")
        sandbox_subnet = parsed_subnet
    except ValueError:
        errors.append("sandbox_gateway must declare a valid IPv4 IPAM subnet")

    runsc_service = services.get(RUNSC_SERVICE)
    runsc_ips: dict[str, str] = {}
    if not isinstance(runsc_service, dict):
        errors.append(f"{RUNSC_SERVICE}: runsc service is missing")
    else:
        if str(runsc_service.get("runtime") or "").strip() != "runsc":
            errors.append(f"{RUNSC_SERVICE}: runtime must remain explicitly set to runsc")
        if _service_networks(runsc_service) != {RUNSC_NETWORK}:
            errors.append(
                f"{RUNSC_SERVICE}: networks must be exactly [{RUNSC_NETWORK!r}]"
            )
        if runsc_service.get("network_mode"):
            errors.append(f"{RUNSC_SERVICE}: network_mode overrides are forbidden")
        runsc_forbidden_env = _environment_names(runsc_service) & RUNSC_FORBIDDEN_ENV
        if runsc_forbidden_env:
            errors.append(
                f"{RUNSC_SERVICE}: privileged or data-plane environment names are forbidden: {sorted(runsc_forbidden_env)}"
            )
        opencode_ip = _network_ipv4(runsc_service, RUNSC_NETWORK)
        if opencode_ip is None:
            errors.append(f"{RUNSC_SERVICE}: stable {RUNSC_NETWORK} IPv4 address is required")
        else:
            runsc_ips[RUNSC_SERVICE] = opencode_ip
        host_entries = _extra_host_entries(runsc_service)
        if set(host_entries) != {name for name, _port in RUNSC_GATEWAYS}:
            errors.append(
                f"{RUNSC_SERVICE}: extra_hosts must contain only the approved tool and model gateway aliases"
            )
        for gateway_name, _port in RUNSC_GATEWAYS:
            gateway_service = services.get(gateway_name)
            gateway_ip = (
                _network_ipv4(gateway_service, RUNSC_NETWORK)
                if isinstance(gateway_service, dict)
                else None
            )
            if gateway_ip is None:
                errors.append(
                    f"{gateway_name}: stable {RUNSC_NETWORK} IPv4 address is required"
                )
                continue
            runsc_ips[gateway_name] = gateway_ip
            if RUNSC_NETWORK not in _service_networks(gateway_service):
                errors.append(f"{gateway_name}: must share {RUNSC_NETWORK} with {RUNSC_SERVICE}")
            if host_entries.get(gateway_name) != gateway_ip:
                errors.append(
                    f"{RUNSC_SERVICE}: extra_hosts must map {gateway_name} to its {RUNSC_NETWORK} IPv4 address"
                )
        orchestrator = services.get("orchestrator-api")
        if not isinstance(orchestrator, dict) or RUNSC_NETWORK not in _service_networks(orchestrator):
            errors.append(f"orchestrator-api: must share {RUNSC_NETWORK} to call {RUNSC_SERVICE}")
        else:
            orchestrator_ip = _network_ipv4(orchestrator, RUNSC_NETWORK)
            if orchestrator_ip is None:
                errors.append(f"orchestrator-api: stable {RUNSC_NETWORK} IPv4 address is required")
            else:
                runsc_ips["orchestrator-api"] = orchestrator_ip
        protected_off_sandbox = ("redis", "postgres", "pgbouncer", "minio")
        for protected_name in protected_off_sandbox:
            protected_service = services.get(protected_name)
            if isinstance(protected_service, dict) and RUNSC_NETWORK in _service_networks(protected_service):
                errors.append(f"{protected_name}: protected service must not join {RUNSC_NETWORK}")
        if any(
            isinstance(volume, str) and "/var/run/docker.sock" in volume
            for volume in runsc_service.get("volumes") or []
        ):
            errors.append(f"{RUNSC_SERVICE}: Docker socket mount is forbidden")

    parsed_ips: dict[str, ipaddress.IPv4Address] = {}
    for service_name, value in runsc_ips.items():
        try:
            parsed = ipaddress.ip_address(value)
            if not isinstance(parsed, ipaddress.IPv4Address):
                raise ValueError("address must be IPv4")
            parsed_ips[service_name] = parsed
            if sandbox_subnet is not None and parsed not in sandbox_subnet:
                errors.append(f"{service_name}: {RUNSC_NETWORK} IPv4 address is outside its subnet")
        except ValueError:
            errors.append(f"{service_name}: invalid {RUNSC_NETWORK} IPv4 address")
    if len(set(parsed_ips.values())) != len(parsed_ips):
        errors.append(f"{RUNSC_NETWORK}: runsc and gateway IPv4 addresses must be unique")
    sandbox_members = {
        service_name
        for service_name, service in services.items()
        if isinstance(service, dict) and RUNSC_NETWORK in _service_networks(service)
    }
    if sandbox_members != RUNSC_NETWORK_MEMBERS:
        errors.append(
            f"{RUNSC_NETWORK}: expected only the OpenCode runtime, its orchestrator management peer, Tool Service and LiteLLM; got {sorted(sandbox_members)}"
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
        "runsc_network": {
            "service": RUNSC_SERVICE,
            "network": RUNSC_NETWORK,
            "subnet": str(sandbox_subnet) if sandbox_subnet is not None else None,
            "static_ipv4": runsc_ips,
            "host_mappings": _extra_host_entries(runsc_service)
            if isinstance(runsc_service, dict)
            else {},
            "network_members": sorted(sandbox_members),
            "allowed_gateways": [
                {"service": service, "port": port} for service, port in RUNSC_GATEWAYS
            ],
            "protected_services": list(RUNSC_PROTECTED),
        },
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

_RUNSC_NODE_TCP_PROBE = r"""
const dns = require('node:dns').promises;
const net = require('node:net');
const host = process.argv[1];
const port = Number(process.argv[2]);
const bounded = (promise, ms) => {
  let timer;
  return Promise.race([
    promise,
    new Promise((_, reject) => {
      timer = setTimeout(() => reject(Object.assign(new Error('timeout'), { code: 'ETIMEDOUT' })), ms);
    }),
  ]).finally(() => clearTimeout(timer));
};
(async () => {
  try {
    const records = await bounded(dns.lookup(host, { all: true, verbatim: true }), 3000);
    if (!records.length) throw Object.assign(new Error('empty DNS result'), { code: 'ENODATA' });
    let connected = false;
    let lastCode = 'ECONNREFUSED';
    for (const record of records) {
      try {
        await bounded(new Promise((resolve, reject) => {
          const socket = net.createConnection({ host: record.address, port, timeout: 2000 }, () => {
            socket.destroy();
            resolve();
          });
          socket.once('timeout', () => {
            socket.destroy();
            reject(Object.assign(new Error('timeout'), { code: 'ETIMEDOUT' }));
          });
          socket.once('error', reject);
        }), 2500);
        connected = true;
        break;
      } catch (error) {
        lastCode = error.code || lastCode;
      }
    }
    process.stdout.write(JSON.stringify({
      reachable: connected,
      addresses: records.map((record) => record.address),
      reason: connected ? null : lastCode,
    }));
    process.exitCode = connected ? 0 : 1;
  } catch (error) {
    const code = typeof error.code === 'string' && /^[A-Z0-9_]+$/.test(error.code)
      ? error.code : 'PROBE_FAILED';
    process.stdout.write(JSON.stringify({ reachable: false, addresses: [], reason: code }));
    process.exitCode = 1;
  }
})();
"""

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


def _probe_runsc_tcp(container_id: str, host: str, port: int) -> dict[str, Any]:
    """Resolve a host using runsc's own NSS path, then make a bounded TCP probe."""
    result = _run(
        ["docker", "exec", container_id, "node", "-e", _RUNSC_NODE_TCP_PROBE, host, str(port)]
    )
    try:
        probe = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {
            "host": host,
            "port": port,
            "reachable": False,
            "resolved_addresses": [],
            "reason": "invalid_probe_output",
        }
    if not isinstance(probe, dict):
        probe = {}
    addresses = probe.get("addresses")
    return {
        "host": host,
        "port": port,
        "reachable": result.returncode == 0 and probe.get("reachable") is True,
        "resolved_addresses": addresses if isinstance(addresses, list) else [],
        "reason": probe.get("reason") if result.returncode != 0 else None,
    }


def _container_ipv4(container_id: str, logical_network: str) -> str | None:
    result = _run(
        ["docker", "inspect", "--format", "{{json .NetworkSettings.Networks}}", container_id]
    )
    if result.returncode != 0:
        return None
    try:
        networks = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(networks, dict):
        return None
    for actual_name, config in networks.items():
        if _logical_network_name(str(actual_name)) != logical_network:
            continue
        if isinstance(config, dict) and isinstance(config.get("IPAddress"), str):
            return str(config["IPAddress"])
    return None


def _inspect_runsc_boundary(protected_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Exercise OpenCode's real runsc resolver and verify its narrow reachability."""
    errors: list[str] = []
    container_id = _docker_container_for_service(RUNSC_SERVICE)
    if not container_id:
        return {
            "service": RUNSC_SERVICE,
            "status": "fail",
            "errors": [f"{RUNSC_SERVICE}: no running container found"],
            "gateway_probes": [],
            "protected_denials": [],
        }

    runtime_result = _run(
        ["docker", "inspect", "--format", "{{.HostConfig.Runtime}}", container_id]
    )
    runtime = runtime_result.stdout.strip() if runtime_result.returncode == 0 else ""
    if runtime != "runsc":
        errors.append(f"{RUNSC_SERVICE}: expected Docker runtime runsc")

    try:
        logical_networks, actual_networks = _logical_networks(container_id)
    except (RuntimeError, OSError):
        logical_networks, actual_networks = set(), set()
        errors.append(f"{RUNSC_SERVICE}: could not inspect attached networks")
    if logical_networks != {RUNSC_NETWORK}:
        errors.append(f"{RUNSC_SERVICE}: must be attached only to {RUNSC_NETWORK}")

    gateway_probes: list[dict[str, Any]] = []
    for service_name, port in RUNSC_GATEWAYS:
        target_id = _docker_container_for_service(service_name)
        target_ip = _container_ipv4(target_id, RUNSC_NETWORK) if target_id else None
        if not target_id or not target_ip:
            probe = {
                "host": service_name,
                "port": port,
                "reachable": False,
                "resolved_addresses": [],
                "reason": "gateway_missing_from_sandbox_network",
            }
        else:
            probe = _probe_runsc_tcp(container_id, service_name, port)
            if target_ip not in probe["resolved_addresses"]:
                probe["reachable"] = False
                probe["reason"] = "host_mapping_does_not_match_gateway_address"
        gateway_probes.append(probe)
        if not probe["reachable"]:
            errors.append(f"{RUNSC_SERVICE}: allowed gateway {service_name}:{port} is unreachable")

    protected_denials: list[dict[str, Any]] = []
    # Only interpret a failed protected-service connection as a denial after
    # proving the same sandbox can reach its allowed control gateways.
    if all(probe["reachable"] for probe in gateway_probes):
        for service_name in RUNSC_PROTECTED:
            target_id = _docker_container_for_service(service_name)
            target_ip = _container_ipv4(target_id, "internal") if target_id else None
            if not target_id or not target_ip:
                protected_denials.append(
                    {"service": service_name, "denied": False, "reason": "service_unavailable"}
                )
                errors.append(f"{RUNSC_SERVICE}: protected service {service_name} is unavailable for denial probe")
                continue
            row = next((item for item in protected_rows if item.get("service") == service_name), None)
            port = int(row["port"]) if isinstance(row, dict) else 0
            probe = _probe_runsc_tcp(container_id, target_ip, port)
            denied = not probe["reachable"]
            protected_denials.append(
                {
                    "service": service_name,
                    "denied": denied,
                    "reason": probe["reason"],
                }
            )
            if not denied:
                errors.append(f"{RUNSC_SERVICE}: protected service {service_name} is reachable")
    else:
        protected_denials = [
            {"service": service_name, "denied": None, "reason": "allowed_gateway_probe_failed"}
            for service_name in RUNSC_PROTECTED
        ]

    return {
        "service": RUNSC_SERVICE,
        "container_id": container_id[:12],
        "runtime": runtime,
        "networks": sorted(actual_networks),
        "logical_networks": sorted(logical_networks),
        "status": "pass" if not errors else "fail",
        "errors": errors,
        "gateway_probes": gateway_probes,
        "protected_denials": protected_denials,
    }


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
    control_plane_policy = policy.get("control_plane") or {}
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
    control_plane_service = str(control_plane_policy.get("service") or "orchestrator-api")
    control_plane_network = str(control_plane_policy.get("network") or "internal")
    control_plane_entry: dict[str, Any] = {
        "service": control_plane_service,
        "required_connections": [],
    }
    try:
        control_plane_id = _docker_container_for_service(control_plane_service)
        if not control_plane_id:
            errors.append(f"{control_plane_service}: no running container found")
            control_plane_entry["status"] = "missing"
        else:
            control_plane_entry["container_id"] = control_plane_id[:12]
            networks, actual_networks = _logical_networks(control_plane_id)
            control_plane_entry["networks"] = sorted(actual_networks)
            if control_plane_network not in networks:
                errors.append(
                    f"{control_plane_service}: live logical networks do not include {control_plane_network}"
                )
            for row in control_plane_policy.get("required_tcp") or []:
                host = str(row["service"])
                port = int(row["port"])
                reachable = _probe_tcp(control_plane_id, host, port)
                control_plane_entry["required_connections"].append(
                    {"host": host, "port": port, "reachable": reachable}
                )
                if not reachable:
                    errors.append(
                        f"{control_plane_service}: required internal service {host}:{port} is unreachable"
                    )
            control_plane_entry["status"] = (
                "pass"
                if control_plane_network in networks
                and all(row["reachable"] for row in control_plane_entry["required_connections"])
                else "fail"
            )
    except (RuntimeError, OSError) as exc:
        control_plane_entry["status"] = "error"
        errors.append(f"{control_plane_service}: live connectivity probe failed: {exc}")

    runsc_entry: dict[str, Any] | None = None
    if isinstance(static_report.get("runsc_network"), dict):
        runsc_entry = _inspect_runsc_boundary(protected_rows)
        errors.extend(runsc_entry["errors"])

    ceo_service = str(policy.get("ceo_service") or CEO_SERVICE)
    for service_name in static_report.get("team_services", []):
        entry_error_count = len(errors)
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
            entry["status"] = "pass" if len(errors) == entry_error_count else "fail"
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
        "control_plane": control_plane_entry,
        "runsc": runsc_entry,
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
