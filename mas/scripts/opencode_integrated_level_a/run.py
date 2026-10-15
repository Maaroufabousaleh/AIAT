"""Launch an isolated, disposable OpenCode/AIAT integration profile.

The profile has no public network and never imports `.env`. All Compose
resources are namespaced under a fresh project name, and cleanup is scoped to
that project with its own volumes and locally built images.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[3]
MAS_ROOT = ROOT / "mas"
COMPOSE_FILE = ROOT / "mas" / "infra" / "compose" / "level-a-opencode-integrated.yml"
DEFAULT_DRIVER = ROOT / "mas" / "scripts" / "opencode_vertical_e2e.py"
PROFILE_LABEL = "opencode-integrated-level-a"
PROJECT_RE = re.compile(r"aiat-level-a-[0-9a-f]{12}\Z")
SUPPORTED_WORKER_NAMES = ("coding_worker", "aiat_deterministic_file_worker")
DETERMINISTIC_WORKER_MANIFEST = MAS_ROOT / "workers" / "aiat_deterministic_file_worker.yaml"
DETERMINISTIC_WORKER_SOURCE = MAS_ROOT / "workers" / "aiat_deterministic_file_worker_process.py"
LITELLM_IMAGE = "ghcr.io/berriai/litellm:main-stable"
BUILD_CONTEXT_PATHS = (
    "alembic.ini",
    "apps/message-router",
    "apps/orchestrator-api",
    "apps/tool-service",
    "companies",
    "docs/provenance/openhands-candidate",
    "infra/docker/Dockerfile.message-router",
    "infra/docker/Dockerfile.opencode",
    "infra/docker/Dockerfile.orchestrator-api",
    "infra/docker/Dockerfile.tool-service",
    "infra/docker/opencode-entrypoint.sh",
    "infra/docker/puppeteer-config.json",
    "migrations",
    "packages/mas-core",
    "packages/mas-tools-sdk",
    "scripts/check_runtime_adapter_conformance.py",
    "scripts/opencode_integrated_level_a/Dockerfile.fixture",
    "scripts/opencode_level_a/fixture.mjs",
    "workers",
)
_BUILD_IGNORED_DIRS = frozenset(
    {
        ".git",
        ".next",
        ".pytest_cache",
        ".pytest_tmp",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
        "playwright-report",
        "test-results",
    }
)

_DOCKER_ENV = frozenset(
    {
        "PATH",
        "HOME",
        "DOCKER_CONFIG",
        "DOCKER_CONTEXT",
        "DOCKER_HOST",
        "DOCKER_TLS_VERIFY",
        "DOCKER_CERT_PATH",
        "XDG_RUNTIME_DIR",
    }
)
_HEALTHY_SERVICES = (
    "postgres",
    "redis",
    "minio",
    "message-router",
    "tool-service",
    "provider-fixture",
    "litellm",
    "opencode-runtime",
    "orchestrator-api",
    "worker-host",
)
_COMPLETED_SERVICES = ("migrate", "minio-init", "workspace-init")


class HarnessError(RuntimeError):
    """A bounded, payload-safe profile setup error."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _docker_environment() -> dict[str, str]:
    env = {key: os.environ[key] for key in _DOCKER_ENV if key in os.environ}
    env["COMPOSE_DISABLE_ENV_FILE"] = "1"
    env["DOCKER_CLI_HINTS"] = "false"
    return env


def _docker_socket_identity(env: dict[str, str]) -> tuple[str, str]:
    """Return the local Docker socket group for the isolated host process.

    The Level A host process is the only container allowed to use the local
    Docker daemon. Accept a verified Unix-socket Docker context (including a
    WSL integration socket), but refuse remote/TLS/named-pipe endpoints.
    """

    inspected = _run(["docker", "context", "inspect"], env=env, timeout=15, check=False)
    if inspected.returncode:
        raise HarnessError("docker_context_inspection_failed")
    try:
        contexts = json.loads(inspected.stdout)
        endpoint = contexts[0]["Endpoints"]["docker"]["Host"]
    except (json.JSONDecodeError, IndexError, KeyError, TypeError) as exc:
        raise HarnessError("docker_context_endpoint_invalid") from exc
    endpoint = str(endpoint).strip()
    override = str(env.get("DOCKER_HOST") or "").strip()
    if override and override != endpoint:
        raise HarnessError("worker_host_requires_local_docker_socket")
    parsed = urllib.parse.urlsplit(endpoint)
    if parsed.scheme != "unix" or parsed.netloc or parsed.query or parsed.fragment:
        raise HarnessError("worker_host_requires_local_docker_socket")
    endpoint_path = urllib.parse.unquote(parsed.path)
    socket_path = Path(endpoint_path)
    if not socket_path.is_absolute():
        raise HarnessError("worker_host_requires_local_docker_socket")
    try:
        socket_path = socket_path.resolve(strict=True)
        socket_stat = socket_path.stat()
    except (OSError, RuntimeError) as exc:
        raise HarnessError("local_docker_socket_unavailable") from exc
    if not stat.S_ISSOCK(socket_stat.st_mode):
        raise HarnessError("local_docker_socket_unavailable")
    return str(socket_stat.st_gid), str(socket_path)


def _run(
    argv: list[str],
    *,
    env: dict[str, str],
    cwd: Path = ROOT,
    timeout: int = 30,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            argv,
            cwd=cwd,
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HarnessError("bounded_command_unavailable_or_timed_out") from exc
    if check and result.returncode:
        raise HarnessError("bounded_command_failed")
    return result


def _select_subnet(occupied: list[str]) -> str:
    occupied_networks: list[ipaddress.IPv4Network] = []
    for raw in occupied:
        try:
            network = ipaddress.ip_network(raw, strict=False)
        except ValueError:
            continue
        if isinstance(network, ipaddress.IPv4Network):
            occupied_networks.append(network)
    pools = (
        "172.28.240.0/20",
        "172.29.240.0/20",
        "172.30.240.0/20",
        "172.31.240.0/20",
        "10.254.240.0/20",
        "10.253.240.0/20",
        "192.168.240.0/20",
    )
    for raw_pool in pools:
        for candidate in ipaddress.ip_network(raw_pool).subnets(new_prefix=28):
            if not any(candidate.overlaps(network) for network in occupied_networks):
                return str(candidate)
    raise HarnessError("no_non_overlapping_private_subnet_available")


def _service_addresses(subnet: str) -> dict[str, str]:
    try:
        network = ipaddress.ip_network(subnet, strict=True)
    except ValueError as exc:
        raise HarnessError("isolated_subnet_invalid") from exc
    if not isinstance(network, ipaddress.IPv4Network) or network.prefixlen != 28 or not network.is_private:
        raise HarnessError("isolated_subnet_must_be_private_ipv4_slash_28")
    hosts = list(network.hosts())
    # Docker reserves the first address as bridge gateway. Six sandbox peers
    # use the following stable addresses; the /28 leaves room for Docker and
    # temporary diagnostics without exhausting the small profile network.
    if len(hosts) != 14:
        raise HarnessError("isolated_subnet_capacity_invalid")
    names = ("FIXTURE", "LITELLM", "TOOL_SERVICE", "OPENCODE", "ORCHESTRATOR", "WORKER_HOST")
    return {
        f"AIAT_LEVEL_A_{name}_IP": str(address)
        for name, address in zip(names, hosts[1 : len(names) + 1], strict=True)
    }


def _available_subnet(env: dict[str, str]) -> str:
    networks = _run(["docker", "network", "ls", "-q"], env=env, timeout=15)
    identifiers = networks.stdout.split()
    occupied: list[str] = []
    if identifiers:
        inspected = _run(["docker", "network", "inspect", *identifiers], env=env, timeout=20)
        try:
            rows = json.loads(inspected.stdout)
            for row in rows:
                for entry in ((row.get("IPAM") or {}).get("Config") or []):
                    if entry.get("Subnet"):
                        occupied.append(str(entry["Subnet"]))
        except (json.JSONDecodeError, TypeError, AttributeError) as exc:
            raise HarnessError("docker_network_inspection_invalid") from exc
    ip_binary = shutil.which("ip")
    if not ip_binary:
        raise HarnessError("iproute2_required_for_non_overlapping_subnet")
    routes = _run([ip_binary, "-j", "route", "show"], env=env, timeout=10)
    try:
        route_rows = json.loads(routes.stdout)
    except json.JSONDecodeError as exc:
        raise HarnessError("host_route_inspection_invalid") from exc
    occupied.extend(
        str(row["dst"])
        for row in route_rows
        if isinstance(row, dict) and row.get("dst") and row.get("dst") != "default"
    )
    return _select_subnet(occupied)


def _compose_base(
    project: str,
    env_file: Path,
    *,
    build_override: Path | None = None,
) -> list[str]:
    if not PROJECT_RE.fullmatch(project):
        raise HarnessError("compose_project_name_not_profile_scoped")
    argv = [
        "docker",
        "compose",
        "--project-name",
        project,
        "--file",
        str(COMPOSE_FILE),
    ]
    if build_override is not None:
        argv.extend(["--file", str(build_override)])
    argv.extend(
        [
            "--project-directory",
            str(COMPOSE_FILE.parent),
        "--env-file",
        str(env_file),
        ]
    )
    return argv


def _copy_ignore(directory: str, names: list[str]) -> set[str]:
    ignored: set[str] = set()
    for name in names:
        path = Path(directory) / name
        if (
            name in _BUILD_IGNORED_DIRS
            or name == ".env"
            or name.startswith(".tmp")
            or (
                path.is_file()
                and (name.endswith((".pyc", ".pyo", ".pyd")) or name.startswith(".env."))
            )
        ):
            ignored.add(name)
    return ignored


def _prepare_build_context(destination: Path) -> Path:
    # The checkout can contain operator-owned temp trees with restrictive
    # permissions. Stage only paths used by the canonical Dockerfiles, so the
    # BuildKit sender never traverses or snapshots unrelated checkout state.
    destination.mkdir(parents=True, exist_ok=False)
    for relative in BUILD_CONTEXT_PATHS:
        source = MAS_ROOT / relative
        target = destination / relative
        if not source.exists():
            raise HarnessError(f"profile_build_context_source_missing:{relative}")
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, target, symlinks=True, ignore=_copy_ignore)
        else:
            shutil.copy2(source, target, follow_symlinks=False)
    return destination


def _write_build_override(path: Path, context: Path) -> Path:
    services = (
        "message-router",
        "tool-service",
        "provider-fixture",
        "opencode-runtime",
        "migrate",
        "orchestrator-api",
    )
    lines = ["services:"]
    for service in services:
        lines.extend(
            [
                f"  {service}:",
                "    build:",
                f"      context: {json.dumps(str(context))}",
            ]
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(0o600)
    return path


def _write_environment(values: dict[str, str], directory: Path) -> Path:
    path = directory / "compose.env"
    with path.open("x", encoding="utf-8") as stream:
        for key, value in sorted(values.items()):
            if "\n" in value or "\r" in value or "\x00" in value:
                raise HarnessError("generated_compose_value_invalid")
            stream.write(f"{key}={value}\n")
    path.chmod(0o600)
    return path


def _render_config(base: list[str], env: dict[str, str]) -> dict[str, Any]:
    result = _run(base + ["config", "--format", "json"], env=env, timeout=30, check=False)
    if result.returncode:
        raise HarnessError("compose_configuration_rejected")
    try:
        config = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise HarnessError("compose_configuration_not_json") from exc
    _assert_profile_config(config)
    return config


def _assert_profile_config(config: dict[str, Any]) -> None:
    services = config.get("services")
    networks = config.get("networks")
    volumes = config.get("volumes")
    expected_services = {
        "postgres",
        "redis",
        "minio",
        "minio-init",
        "message-router",
        "tool-service",
        "provider-fixture",
        "litellm",
        "opencode-runtime",
        "migrate",
        "workspace-init",
        "orchestrator-api",
        "worker-host",
    }
    if not isinstance(services, dict) or set(services) != expected_services:
        raise HarnessError("compose_service_set_changed")
    if not isinstance(networks, dict) or set(networks) != {"control", "sandbox_gateway"}:
        raise HarnessError("compose_network_set_changed")
    if not isinstance(volumes, dict) or set(volumes) != {
        "level_a_postgres_data",
        "level_a_redis_data",
        "level_a_minio_data",
        "level_a_workspace",
    }:
        raise HarnessError("compose_volume_set_changed")
    if any(value.get("internal") is not True for value in networks.values()):
        raise HarnessError("profile_network_must_block_external_egress")
    project_prefix = r"aiat-level-a-[0-9a-f]{12}_"
    for name, volume in volumes.items():
        volume_name = str(volume.get("name") or "")
        if volume.get("external") or not re.fullmatch(project_prefix + re.escape(name), volume_name):
            raise HarnessError(f"profile_volume_not_project_scoped:{name}")
    if config.get("secrets"):
        raise HarnessError("ambient_compose_secrets_are_forbidden")

    namespace = str(config.get("name") or "")
    expected_image_suffixes = {
        "message-router": "message-router:local",
        "tool-service": "tool-service:local",
        "provider-fixture": "provider-fixture:local",
        "opencode-runtime": "opencode-runtime:1.17.13",
        "migrate": "orchestrator-api:local",
        "orchestrator-api": "orchestrator-api:local",
        "worker-host": "orchestrator-api:local",
    }
    if not PROJECT_RE.fullmatch(namespace):
        raise HarnessError("profile_compose_project_name_invalid")
    for service_name, suffix in expected_image_suffixes.items():
        if services[service_name].get("image") != f"{namespace}/{suffix}":
            raise HarnessError(f"profile_image_not_uniquely_namespaced:{service_name}")

    sandbox_services = {"provider-fixture", "litellm", "tool-service", "opencode-runtime", "orchestrator-api", "worker-host"}
    control_services = {"postgres", "redis", "minio", "minio-init", "message-router", "tool-service", "migrate", "orchestrator-api", "worker-host"}
    for name, service in services.items():
        if service.get("env_file") or service.get("secrets") or service.get("ports"):
            raise HarnessError(f"ambient_or_host_exposure_forbidden:{name}")
        if service.get("privileged"):
            raise HarnessError(f"privileged_service_forbidden:{name}")
        if service.get("cap_add") or service.get("devices"):
            raise HarnessError(f"extra_host_authority_forbidden:{name}")
        socket_mounts = [
            volume
            for volume in service.get("volumes") or []
            if isinstance(volume, dict)
            and (
                "/var/run/docker.sock" in str(volume.get("source") or "")
                or "/var/run/docker.sock" in str(volume.get("target") or "")
            )
        ]
        if name == "worker-host":
            if (
                len(socket_mounts) != 1
                or socket_mounts[0].get("type") != "bind"
                or not Path(str(socket_mounts[0].get("source") or "")).is_absolute()
                or socket_mounts[0].get("target") != "/var/run/docker.sock"
                or service.get("user") != "10001:10001"
                or service.get("read_only") is not True
                or set(service.get("cap_drop") or []) != {"ALL"}
                or "no-new-privileges:true" not in (service.get("security_opt") or [])
                or set(service.get("networks") or []) != {"control", "sandbox_gateway"}
                or service.get("privileged") is True
            ):
                raise HarnessError("worker_host_docker_authority_boundary_invalid")
            group_add = service.get("group_add") or []
            if len(group_add) != 1 or not str(group_add[0]).isdigit():
                raise HarnessError("worker_host_docker_socket_group_invalid")
            if (service.get("environment") or {}).get("AIAT_LOCAL_HOST_QUEUE_ENABLED") != "true":
                raise HarnessError("worker_host_queue_opt_in_missing")
        elif socket_mounts or "/var/run/docker.sock" in json.dumps(service).lower():
            raise HarnessError(f"docker_socket_mount_forbidden:{name}")
        build = service.get("build")
        if build:
            context = Path(str(build.get("context") or ""))
            dockerfile = Path(str(build.get("dockerfile") or "Dockerfile"))
            if not context.is_dir() or not (context / dockerfile).is_file():
                raise HarnessError(f"profile_build_recipe_missing:{name}")
        attached = set(service.get("networks") or [])
        environment = service.get("environment") or {}
        if "DOCKER_HOST" in environment or "DOCKER_CONTEXT" in environment:
            raise HarnessError(f"docker_daemon_environment_forbidden:{name}")
        if name in sandbox_services and "sandbox_gateway" not in attached:
            raise HarnessError(f"sandbox_gateway_membership_missing:{name}")
        if name in control_services and "control" not in attached:
            raise HarnessError(f"control_network_membership_missing:{name}")
        if name in {"postgres", "redis", "minio", "minio-init", "message-router", "migrate"} and "sandbox_gateway" in attached:
            raise HarnessError(f"protected_service_on_sandbox_network:{name}")
    runtime = services["opencode-runtime"]
    if runtime.get("runtime") != "runsc":
        raise HarnessError("opencode_runtime_must_use_runsc")
    if set(runtime.get("networks") or []) != {"sandbox_gateway"}:
        raise HarnessError("opencode_must_not_join_control_network")
    if runtime.get("read_only") is not True:
        raise HarnessError("opencode_runtime_filesystem_must_be_read_only")
    if services["orchestrator-api"].get("environment", {}).get("AIAT_ALLOW_EXTERNAL_PROVIDER_DISPATCH") != "false":
        raise HarnessError("external_provider_dispatch_must_remain_disabled")
    if services["worker-host"].get("environment", {}).get("AIAT_ALLOW_EXTERNAL_PROVIDER_DISPATCH") != "false":
        raise HarnessError("external_provider_dispatch_must_remain_disabled")
    if services["orchestrator-api"].get("environment", {}).get("AIAT_LOCAL_HOST_QUEUE_ENABLED") != "false":
        raise HarnessError("api_must_not_own_local_host_queue")
    if services["litellm"].get("pull_policy") != "never":
        raise HarnessError("gateway_image_must_not_be_pulled_implicitly")

    gateway_key = services["litellm"].get("environment", {}).get("LITELLM_MASTER_KEY")
    if not gateway_key:
        raise HarnessError("local_gateway_master_key_missing")
    gateway_clients = {
        "tool-service": "LLM_API_KEY",
        "orchestrator-api": "LLM_API_KEY",
        "opencode-runtime": "AIAT_GATEWAY_API_KEY",
    }
    for service_name, key_name in gateway_clients.items():
        client_key = services[service_name].get("environment", {}).get(key_name)
        if client_key != gateway_key:
            raise HarnessError(f"local_gateway_client_key_mismatch:{service_name}")

    for name in ("tool-service", "orchestrator-api", "opencode-runtime"):
        env = services[name].get("environment") or {}
        if env.get("LLM_GATEWAY_URL", env.get("AIAT_GATEWAY_URL")) not in {
            "http://litellm:4000",
        }:
            raise HarnessError(f"local_gateway_route_invalid:{name}")
    lite_config = Path("mas/scripts/opencode_integrated_level_a/litellm_config.yaml")
    content = lite_config.read_text(encoding="utf-8")
    if "http://provider-fixture:8000/v1" not in content or "model: openai/fixture-model" not in content:
        raise HarnessError("gateway_must_have_only_the_deterministic_fixture_route")
    if re.search(r"https?://(?!provider-fixture:)[^\s]+", content):
        raise HarnessError("gateway_config_contains_non_fixture_endpoint")


def _container_id(base: list[str], service: str, env: dict[str, str]) -> str:
    result = _run(base + ["ps", "--all", "--quiet", service], env=env, timeout=15, check=False)
    if result.returncode:
        return ""
    return result.stdout.strip().splitlines()[-1:][0] if result.stdout.strip() else ""


def _wait_for_container_state(
    base: list[str],
    service: str,
    env: dict[str, str],
    *,
    expected: str,
    timeout_seconds: int = 180,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        identifier = _container_id(base, service, env)
        if identifier:
            result = _run(
                [
                    "docker",
                    "inspect",
                    "--format",
                    "{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{end}}|{{.State.ExitCode}}|{{index .Config.Labels \"com.docker.compose.project\"}}|{{index .Config.Labels \"com.aiat.profile\"}}",
                    identifier,
                ],
                env=env,
                timeout=10,
                check=False,
            )
            if result.returncode == 0:
                parts = result.stdout.strip().split("|", maxsplit=4)
                if len(parts) == 5:
                    state, health, exit_code, compose_project, profile = parts
                    if profile != PROFILE_LABEL:
                        raise HarnessError(f"container_profile_label_mismatch:{service}")
                    if expected == "healthy" and state == "running" and health == "healthy":
                        return
                    if expected == "completed" and state == "exited" and exit_code == "0":
                        return
                    if state in {"dead", "removing"} or (state == "exited" and expected == "healthy"):
                        raise HarnessError(f"profile_service_failed:{service}")
                    if compose_project != base[base.index("--project-name") + 1]:
                        raise HarnessError("compose_project_label_mismatch")
        time.sleep(1)
    raise HarnessError(f"profile_service_state_timeout:{service}:{expected}")


def _check_local_base_images(config: dict[str, Any], env: dict[str, str]) -> None:
    services = config.get("services") or {}
    built_images = {
        str(service.get("image") or "")
        for service in services.values()
        if service.get("build") and service.get("image")
    }
    for service_name, service in services.items():
        if (
            service.get("build")
            or str(service.get("image") or "") in built_images
            or service.get("pull_policy") != "never"
        ):
            continue
        image = str(service.get("image") or "").strip()
        if not image:
            raise HarnessError(f"profile_image_reference_missing:{service_name}")
        result = _run(["docker", "image", "inspect", image], env=env, timeout=10, check=False)
        if result.returncode:
            raise HarnessError(f"local_base_image_missing_pull_is_disabled:{service_name}")


def _safe_compose_start_reason(output: str) -> str:
    """Map Compose diagnostics to fixed reason codes without retaining text."""

    normalized = output.lower()
    categories = (
        ("permission denied", "host_permission_denied"),
        ("operation not permitted", "host_permission_denied"),
        ("mounts denied", "host_mount_rejected"),
        ("invalid mount config", "host_mount_rejected"),
        ("bind source path does not exist", "host_mount_rejected"),
        ("no such file or directory", "host_path_unavailable"),
        ("source path must be absolute", "host_path_unavailable"),
        ("address already in use", "host_port_conflict"),
        ("port is already allocated", "host_port_conflict"),
        ("pull access denied", "base_image_unavailable"),
        ("manifest unknown", "base_image_unavailable"),
        ("not found: manifest", "base_image_unavailable"),
        ("no such runtime", "container_runtime_unavailable"),
        ("unknown runtime", "container_runtime_unavailable"),
        ("failed to create shim", "container_runtime_unavailable"),
    )
    for needle, reason in categories:
        if needle in normalized:
            return reason
    services = (
        "worker-host",
        "orchestrator-api",
        "opencode-runtime",
        "tool-service",
        "message-router",
        "provider-fixture",
        "litellm",
        "postgres",
        "redis",
        "minio",
    )
    failure_markers = ("error", "failed", "exited", "unhealthy", "cannot", "unable", "denied")
    for line in normalized.splitlines():
        if not any(marker in line for marker in failure_markers):
            continue
        if "runsc" in line or "runtime" in line and "unavailable" in line:
            return "sandbox_runtime_start_failed"
        for service in services:
            if service in line:
                return f"service_start_rejected:{service.replace('-', '_')}"
    if "network" in normalized and "not found" in normalized:
        return "network_setup_failed"
    return "compose_start_rejected_unclassified"


def _profile_image_tags(namespace: str) -> tuple[str, ...]:
    if not PROJECT_RE.fullmatch(namespace):
        raise HarnessError("profile_image_namespace_invalid")
    return (
        f"{namespace}/message-router:local",
        f"{namespace}/tool-service:local",
        f"{namespace}/provider-fixture:local",
        f"{namespace}/opencode-runtime:1.17.13",
        f"{namespace}/orchestrator-api:local",
    )


def _ensure_profile_images_unused(namespace: str, env: dict[str, str]) -> None:
    for image in _profile_image_tags(namespace):
        result = _run(["docker", "image", "inspect", image], env=env, timeout=10, check=False)
        if result.returncode == 0:
            raise HarnessError("disposable_image_namespace_collision")


def _cleanup_profile_images(namespace: str, env: dict[str, str]) -> None:
    for image in _profile_image_tags(namespace):
        existing = _run(["docker", "image", "inspect", image], env=env, timeout=10, check=False)
        if not existing.returncode:
            _run(["docker", "image", "rm", image], env=env, timeout=30, check=False)


def _verify_profile_cleanup(project: str, env: dict[str, str]) -> None:
    for kind, command in (
        ("containers", ["docker", "ps", "--all", "--quiet"]),
        ("networks", ["docker", "network", "ls", "--quiet"]),
        ("volumes", ["docker", "volume", "ls", "--quiet"]),
    ):
        result = _run(
            command + ["--filter", f"label=com.docker.compose.project={project}"],
            env=env,
            timeout=15,
            check=False,
        )
        if result.returncode or result.stdout.strip():
            raise HarnessError(f"profile_cleanup_left_{kind}")
    for image in _profile_image_tags(project):
        result = _run(["docker", "image", "inspect", image], env=env, timeout=10, check=False)
        if result.returncode == 0:
            raise HarnessError("profile_cleanup_left_local_image")


def _select_worker_id(workers: Any, worker_name: str) -> str | None:
    if worker_name not in SUPPORTED_WORKER_NAMES:
        raise HarnessError("worker_name_unsupported")
    if not isinstance(workers, list):
        raise HarnessError("worker_manifest_list_invalid")
    matches = [
        row
        for row in workers
        if isinstance(row, dict) and str(row.get("name") or "") == worker_name
    ]
    if not matches:
        return None
    if len(matches) != 1:
        raise HarnessError("worker_manifest_ambiguous")
    worker_id = str(matches[0].get("id") or "")
    if not re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", worker_id):
        raise HarnessError("worker_identifier_invalid")
    return worker_id


def _resolve_worker_id(base: list[str], env: dict[str, str], worker_name: str) -> str | None:
    # Resolve the database UUID through the normal authenticated API. Never
    # infer a worker ID from an image, manifest file, or database internals.
    if worker_name not in SUPPORTED_WORKER_NAMES:
        raise HarnessError("worker_name_unsupported")
    lookup = (
        "import json,os,urllib.request; "
        f"name={json.dumps(worker_name)}; "
        "key=os.environ.get('AIAT_LEVEL_A_OPERATOR_API_KEY',''); "
        "request=urllib.request.Request('http://127.0.0.1:8000/capabilities/workers', "
        "headers={'X-API-Key':key,'Accept':'application/json'}); "
        "response=urllib.request.urlopen(request,timeout=10); "
        "rows=json.load(response); "
        "matches=[row for row in rows if isinstance(row,dict) and row.get('name')==name]; "
        "print(json.dumps(matches))"
    )
    result = _run(
        base + ["exec", "-T", "orchestrator-api", "python", "-c", lookup],
        env=env,
        timeout=20,
        check=False,
    )
    if result.returncode:
        raise HarnessError("worker_api_lookup_failed")
    try:
        rows = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise HarnessError("worker_api_lookup_invalid") from exc
    return _select_worker_id(rows, worker_name)


def _deterministic_worker_image_identity() -> dict[str, str]:
    """Load and cross-check the source-bound OCI reference without resolving tags."""

    try:
        manifest = yaml.safe_load(DETERMINISTIC_WORKER_MANIFEST.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise HarnessError("deterministic_worker_manifest_unreadable") from exc
    if not isinstance(manifest, dict):
        raise HarnessError("deterministic_worker_manifest_invalid")
    metadata = manifest.get("metadata")
    runtime = manifest.get("runtime")
    adapter = runtime.get("adapter_config") if isinstance(runtime, dict) else None
    provenance = manifest.get("source_provenance")
    if not all(isinstance(value, dict) for value in (metadata, runtime, adapter, provenance)):
        raise HarnessError("deterministic_worker_manifest_invalid")

    version = str(metadata.get("version") or "")
    reference = str(adapter.get("image") or "")
    digest = str(provenance.get("oci_image_digest") or "")
    revision = str(provenance.get("commit_sha") or "")
    source_fingerprint = str(provenance.get("runtime_fingerprint") or "")
    version_pin = str(metadata.get("version_pin") or "")
    if metadata.get("id") != "aiat_deterministic_file_worker" or runtime.get("transport") != "oci":
        raise HarnessError("deterministic_worker_manifest_identity_invalid")
    digest_match = re.fullmatch(r"sha256:([0-9a-f]{64})", digest)
    source_match = re.fullmatch(r"sha256:([0-9a-f]{64})", source_fingerprint)
    if not digest_match or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise HarnessError("deterministic_worker_manifest_digest_invalid")
    expected_reference = f"aiat-deterministic-file-worker@{digest}"
    if (
        reference != expected_reference
        or provenance.get("transport_type") != "oci"
        or provenance.get("exact_release") != version
        or metadata.get("source_revision") != revision
        or version_pin != f"{version}+sha256.{digest_match.group(1)}"
        or adapter.get("runtime_version") != f"aiat-deterministic-file-worker/{version}"
        or not source_match
    ):
        raise HarnessError("deterministic_worker_manifest_identity_mismatch")
    if DETERMINISTIC_WORKER_SOURCE.is_symlink() or not DETERMINISTIC_WORKER_SOURCE.is_file():
        raise HarnessError("deterministic_worker_source_unavailable")
    try:
        source_sha256 = hashlib.sha256(DETERMINISTIC_WORKER_SOURCE.read_bytes()).hexdigest()
    except OSError as exc:
        raise HarnessError("deterministic_worker_source_unavailable") from exc
    if source_sha256 != source_match.group(1):
        raise HarnessError("deterministic_worker_source_fingerprint_mismatch")
    return {
        "image_reference": reference,
        "image_digest": digest,
        "version": version,
        "source_revision": revision,
        "source_sha256": source_sha256,
    }


def _verify_local_deterministic_worker_image(env: dict[str, str]) -> dict[str, Any]:
    """Require the manifest's exact image digest and source labels in the daemon."""

    identity = _deterministic_worker_image_identity()
    reference = identity["image_reference"]
    digests = _run(
        ["docker", "image", "inspect", "--format", "{{json .RepoDigests}}", reference],
        env=env,
        timeout=10,
        check=False,
    )
    if digests.returncode:
        raise HarnessError("deterministic_worker_image_digest_not_present_locally")
    try:
        repo_digests = json.loads(digests.stdout)
    except json.JSONDecodeError as exc:
        raise HarnessError("deterministic_worker_image_inspection_invalid") from exc
    if not isinstance(repo_digests, list) or reference not in repo_digests:
        raise HarnessError("deterministic_worker_image_digest_mismatch")

    inspected_labels = _run(
        ["docker", "image", "inspect", "--format", "{{json .Config.Labels}}", reference],
        env=env,
        timeout=10,
        check=False,
    )
    if inspected_labels.returncode:
        raise HarnessError("deterministic_worker_image_digest_not_present_locally")
    try:
        labels = json.loads(inspected_labels.stdout)
    except json.JSONDecodeError as exc:
        raise HarnessError("deterministic_worker_image_inspection_invalid") from exc
    if not isinstance(labels, dict):
        raise HarnessError("deterministic_worker_image_labels_missing")
    expected_labels = {
        "org.opencontainers.image.version": identity["version"],
        "org.opencontainers.image.revision": identity["source_revision"],
        "com.aiat.worker.source-sha256": identity["source_sha256"],
    }
    if any(labels.get(key) != value for key, value in expected_labels.items()):
        raise HarnessError("deterministic_worker_image_source_labels_mismatch")
    return {**identity, "local_digest_verified": True, "source_labels_verified": True}


def _fixture_request_count(base: list[str], env: dict[str, str]) -> int:
    # Read only the counter from the fixture's in-memory report endpoint. This
    # remains inside its isolated container and never prints request content.
    script = (
        "fetch('http://127.0.0.1:8000/__fixture/report').then(async response => {"
        "if (!response.ok) process.exit(2); "
        "const report = await response.json(); "
        "if (!Number.isSafeInteger(report.request_count) || report.request_count < 0) process.exit(3); "
        "process.stdout.write(String(report.request_count));"
        "}).catch(() => process.exit(4));"
    )
    result = _run(
        base + ["exec", "-T", "provider-fixture", "node", "-e", script],
        env=env,
        timeout=15,
        check=False,
    )
    try:
        count = int(result.stdout.strip())
    except ValueError as exc:
        raise HarnessError("provider_fixture_counter_unavailable") from exc
    if result.returncode or count < 0:
        raise HarnessError("provider_fixture_counter_invalid")
    return count


def _pending_worker_gate_verified(driver_result: dict[str, Any]) -> bool:
    payload = driver_result.get("result")
    stages = payload.get("stages") if isinstance(payload, dict) else None
    worker = stages.get("WORKER_SELECTED") if isinstance(stages, dict) else None
    submission = stages.get("WORKER_RUN_SUBMITTED") if isinstance(stages, dict) else None
    worker_evidence = worker.get("evidence") if isinstance(worker, dict) else None
    readiness = (
        worker_evidence.get("readiness")
        if isinstance(worker_evidence, dict)
        else None
    )
    blocker_codes = (
        set(readiness.get("blocker_codes") or [])
        if isinstance(readiness, dict)
        and isinstance(readiness.get("blocker_codes"), list)
        else set()
    )
    admission_blockers = {
        "worker_not_active",
        "worker_version_pin_missing",
        "worker_evaluation_not_approved",
        "active_shell_version_id_missing",
        "active_adapter_id_missing",
        "active_skill_bundle_id_missing",
        "worker_not_model_backed",
        "sandbox_profile_invalid",
        "sandbox_profile_not_hardened",
        "worker_health_not_ready",
    }
    worker_gate_blocked = (
        isinstance(worker, dict)
        and worker.get("reason")
        in {"WORKER_NOT_ACTIVE", "WORKER_EVALUATION_NOT_APPROVED"}
    ) or (
        isinstance(worker, dict)
        and worker.get("reason") == "CANONICAL_WORKER_READINESS_BLOCKED"
        and isinstance(readiness, dict)
        and readiness.get("status") == "blocked"
        and bool(blocker_codes.intersection(admission_blockers))
    )
    return (
        driver_result.get("exit_code") == 2
        and isinstance(worker, dict)
        and worker.get("status") == "BLOCKED"
        and worker_gate_blocked
        and isinstance(submission, dict)
        and submission.get("status") == "NOT_RUN"
        and submission.get("reason") == "STAGE_NOT_REACHED"
        and payload.get("worker_run_id") is None
    )


def _api_project_cleanup_verified(driver_result: dict[str, Any]) -> bool:
    payload = driver_result.get("result")
    stages = payload.get("stages") if isinstance(payload, dict) else None
    cleanup = stages.get("CLEANUP_VERIFIED") if isinstance(stages, dict) else None
    evidence = cleanup.get("evidence") if isinstance(cleanup, dict) else None
    return (
        isinstance(cleanup, dict)
        and cleanup.get("status") == "PASS"
        and isinstance(evidence, dict)
        and evidence.get("project_deleted") is True
    )


def _worker_artifact_completion_blocked_verified(
    driver_result: dict[str, Any],
    *,
    worker_id: str | None,
    provider_fixture_calls: int,
) -> bool:
    """Recognize a real artifact/evidence pass stopped at governed completion."""

    payload = driver_result.get("result")
    stages = payload.get("stages") if isinstance(payload, dict) else None
    if (
        driver_result.get("exit_code") != 2
        or provider_fixture_calls != 0
        or not worker_id
        or not isinstance(stages, dict)
        or not payload.get("project_id")
        or not payload.get("worker_run_id")
    ):
        return False
    required_pass = (
        "PROJECT_CREATED",
        "WORKER_SELECTED",
        "WORKER_RUN_SUBMITTED",
        "HOST_RESERVED",
        "HOST_CLAIMED",
        "HOST_CONSUMER_EXECUTED",
        "RUNTIME_STARTED",
        "TOOL_SERVICE_CALLED",
        "TOOL_EFFECT_PERSISTED",
        "FILE_GENERATED",
        "ARTIFACT_REGISTERED",
        "WORKER_RUN_SETTLED",
        "EVIDENCE_AVAILABLE",
    )
    if any(
        not isinstance(stages.get(name), dict) or stages[name].get("status") != "PASS"
        for name in required_pass
    ):
        return False

    selected = stages["WORKER_SELECTED"].get("evidence")
    if not isinstance(selected, dict) or (
        selected.get("name") or selected.get("worker_name")
    ) != "aiat_deterministic_file_worker" or str(selected.get("worker_id") or "") != worker_id:
        return False
    project_created = stages["PROJECT_CREATED"].get("evidence")
    submission = stages["WORKER_RUN_SUBMITTED"].get("evidence")
    settled = stages["WORKER_RUN_SETTLED"].get("evidence")
    tool_effects = stages["TOOL_EFFECT_PERSISTED"].get("evidence")
    tool_calls = stages["TOOL_SERVICE_CALLED"].get("evidence")
    files = stages["FILE_GENERATED"].get("evidence")
    artifacts = stages["ARTIFACT_REGISTERED"].get("evidence")
    evidence = stages["EVIDENCE_AVAILABLE"].get("evidence")
    host_reservation = stages["HOST_RESERVED"].get("evidence")
    host_execution = stages["HOST_CONSUMER_EXECUTED"].get("evidence")
    runtime = stages["RUNTIME_STARTED"].get("evidence")
    if (
        not isinstance(project_created, dict)
        or str(project_created.get("project_id") or "") != str(payload["project_id"])
        or not isinstance(submission, dict)
        or str(submission.get("run_id") or "") != str(payload["worker_run_id"])
        or not isinstance(settled, dict)
        or str(settled.get("run_id") or "") != str(payload["worker_run_id"])
        or str(settled.get("state") or "").upper() != "SUCCEEDED"
        or not isinstance(tool_effects, dict)
        or not isinstance(tool_effects.get("completed_effect_count"), int)
        or isinstance(tool_effects.get("completed_effect_count"), bool)
        or tool_effects["completed_effect_count"] < 1
        or not isinstance(tool_calls, dict)
        or not isinstance(tool_calls.get("effect_count"), int)
        or isinstance(tool_calls.get("effect_count"), bool)
        or tool_calls["effect_count"] < 1
        or not isinstance(tool_calls.get("tool_names"), list)
        or "file_write" not in tool_calls["tool_names"]
        or not isinstance(host_reservation, dict)
        or not host_reservation.get("host_binding_id")
        or not host_reservation.get("host_id")
        or not isinstance(host_execution, dict)
        or not host_execution.get("claim_transition_id")
        or not host_execution.get("dispatch_transition_id")
        or not host_execution.get("running_transition_id")
        or host_execution.get("running_transition_actor") != "worker-run-controller"
        or host_execution.get("claim_preceded_running") is not True
        or not isinstance(runtime, dict)
        or runtime.get("accepted_event_observed") is not True
        or not isinstance(files, dict)
        or not isinstance(files.get("files"), list)
        or not files["files"]
        or not isinstance(artifacts, dict)
        or not isinstance(artifacts.get("artifacts"), list)
        or not artifacts["artifacts"]
        or not isinstance(evidence, dict)
        or evidence.get("required_checks_pass") is not True
        or not isinstance(evidence.get("artifact_ref_count"), int)
        or isinstance(evidence.get("artifact_ref_count"), bool)
        or evidence["artifact_ref_count"] < 1
    ):
        return False
    if not all(
        isinstance(row, dict)
        and row.get("artifact_id")
        and re.fullmatch(r"[0-9a-f]{64}", str(row.get("sha256") or ""))
        and isinstance(row.get("size_bytes"), int)
        and not isinstance(row.get("size_bytes"), bool)
        and row["size_bytes"] > 0
        for row in artifacts["artifacts"]
    ):
        return False
    artifact_ids = {str(row["artifact_id"]) for row in artifacts["artifacts"]}
    evidence_artifact_ids = (
        {str(value) for value in evidence.get("artifact_ids") if value is not None}
        if isinstance(evidence, dict) and isinstance(evidence.get("artifact_ids"), list)
        else set()
    )
    if not artifact_ids or not artifact_ids.issubset(evidence_artifact_ids):
        return False

    for name in ("MODEL_REQUEST_RESOLVED", "LOCAL_MODEL_RESPONSE_RECEIVED"):
        stage = stages.get(name)
        if (
            not isinstance(stage, dict)
            or stage.get("status") != "N/A"
            or stage.get("reason") != "NON_MODEL_WORKER"
        ):
            return False
    completion = stages.get("PROJECT_COMPLETED")
    completion_evidence = completion.get("evidence") if isinstance(completion, dict) else None
    if (
        not isinstance(completion, dict)
        or completion.get("status") != "BLOCKED"
        or completion.get("reason") != "PROJECT_WORKFLOW_COMPLETION_REQUIRES_ITS_GOVERNED_MILESTONES"
        or not isinstance(completion_evidence, dict)
        or str(completion_evidence.get("state") or "").upper() == "COMPLETED"
        or completion_evidence.get("evidence_chain_passed") is not True
        or not _api_project_cleanup_verified(driver_result)
    ):
        return False
    return True


def _classify_driver_result(
    driver_result: dict[str, Any],
    *,
    worker_name: str,
    worker_id: str | None,
    provider_fixture_calls: int,
) -> tuple[str, str | None, bool]:
    exit_code = driver_result.get("exit_code")
    if exit_code == 0:
        return "DRIVER_PASS", None, False
    if exit_code == 2 and _worker_artifact_completion_blocked_verified(
        driver_result,
        worker_id=worker_id,
        provider_fixture_calls=provider_fixture_calls,
    ) and worker_name == "aiat_deterministic_file_worker":
        return "DRIVER_PARTIAL", "WORKER_ARTIFACT_PASS_PROJECT_COMPLETION_BLOCKED", True
    if exit_code == 2:
        return "DRIVER_BLOCKED", None, False
    return "DRIVER_FAIL", None, False


def _require_runsc(env: dict[str, str]) -> None:
    result = _run(["docker", "info", "--format", "{{json .Runtimes}}"], env=env, timeout=15, check=False)
    if result.returncode:
        raise HarnessError("docker_daemon_runtime_inspection_failed")
    try:
        runtimes = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise HarnessError("docker_daemon_runtime_inspection_invalid") from exc
    if not isinstance(runtimes, dict) or "runsc" not in runtimes:
        raise HarnessError("runsc_runtime_not_registered")


def _safe_driver_result(payload: Any, generated_values: set[str]) -> Any:
    if isinstance(payload, dict):
        safe: dict[str, Any] = {}
        blocked_names = ("authorization", "password", "secret", "token", "credential", "api_key", "key_value")
        for key, value in payload.items():
            if any(word in str(key).lower() for word in blocked_names):
                continue
            if str(key).lower() in {"content", "raw_request", "raw_response", "prompt"}:
                continue
            safe[str(key)] = _safe_driver_result(value, generated_values)
        return safe
    if isinstance(payload, list):
        return [_safe_driver_result(value, generated_values) for value in payload]
    if isinstance(payload, str):
        for value in generated_values:
            payload = payload.replace(value, "[REDACTED]")
        return payload[:2_000]
    if payload is None or isinstance(payload, (bool, int, float)):
        return payload
    return f"<{type(payload).__name__}>"


def _run_driver(
    base: list[str],
    env: dict[str, str],
    driver: Path,
    values: dict[str, str],
    worker_id: str | None,
    worker_name: str,
) -> dict[str, Any]:
    try:
        driver = driver.resolve(strict=True)
    except OSError as exc:
        raise HarnessError("api_acceptance_driver_missing") from exc
    try:
        driver.relative_to(ROOT)
    except ValueError as exc:
        raise HarnessError("api_acceptance_driver_must_be_in_repository") from exc
    if not driver.is_file() or driver.stat().st_size > 2_000_000:
        raise HarnessError("api_acceptance_driver_invalid")

    destination = "/tmp/aiat_opencode_vertical_e2e.py"
    copied = _run(base + ["cp", str(driver), f"orchestrator-api:{destination}"], env=env, timeout=20, check=False)
    if copied.returncode:
        raise HarnessError("api_acceptance_driver_copy_failed")
    try:
        exec_result = _run(
            base
            + [
                "exec",
                "-T",
                "-e",
                f"AIAT_LEVEL_A_WORKER_ID={worker_id or 'not-found'}",
                "-e",
                f"AIAT_LEVEL_A_WORKER_NAME={worker_name}",
                "-e",
                "AIAT_LEVEL_A_PROVIDER_BOUNDARY=local_fixture",
                "orchestrator-api",
                "python",
                destination,
            ],
            env=env,
            timeout=900,
            check=False,
        )
    finally:
        _run(base + ["exec", "-T", "orchestrator-api", "rm", "-f", destination], env=env, timeout=15, check=False)

    raw = exec_result.stdout.strip()
    raw_hash = hashlib.sha256(exec_result.stdout.encode("utf-8", errors="replace")).hexdigest()
    try:
        result_payload = json.loads(raw)
    except json.JSONDecodeError:
        raise HarnessError(f"api_driver_output_not_json:{exec_result.returncode}:{raw_hash}") from None
    return {
        "exit_code": exec_result.returncode,
        "output_sha256": raw_hash,
        "result": _safe_driver_result(result_payload, set(values.values())),
    }


def _report(status: str, **kwargs: Any) -> dict[str, Any]:
    return {
        "schema": "aiat.opencode-integrated-level-a.v1",
        "status": status,
        "production_worker_gate_changed": False,
        "external_provider_dispatch_enabled": False,
        **kwargs,
    }


def run_profile(
    *,
    stack_only: bool,
    config_only: bool,
    driver: Path,
    worker_name: str = "coding_worker",
) -> dict[str, Any]:
    if worker_name not in SUPPORTED_WORKER_NAMES:
        raise HarnessError("worker_name_unsupported")
    env = _docker_environment()
    project = f"aiat-level-a-{secrets.token_hex(6)}"
    with tempfile.TemporaryDirectory(prefix="aiat-opencode-integrated-level-a-") as temporary:
        tempdir = Path(temporary)
        subnet = "10.254.240.0/28" if config_only else _available_subnet(env)
        docker_gid, docker_socket_path = (
            ("10001", "/tmp/aiat-level-a-docker.sock")
            if config_only
            else _docker_socket_identity(env)
        )
        values = {
            **_service_addresses(subnet),
            "AIAT_LEVEL_A_SUBNET": subnet,
            "AIAT_LEVEL_A_IMAGE_NAMESPACE": project,
            # check-only renders synthetic socket metadata. Real runs derive
            # both values from the exact local Unix socket used by Docker.
            "AIAT_LEVEL_A_DOCKER_GID": docker_gid,
            "AIAT_LEVEL_A_DOCKER_SOCKET_PATH": docker_socket_path,
            "AIAT_LEVEL_A_LITELLM_IMAGE": LITELLM_IMAGE,
            "AIAT_LEVEL_A_DB_PASSWORD": secrets.token_urlsafe(32),
            "AIAT_LEVEL_A_MINIO_PASSWORD": secrets.token_urlsafe(32),
            "AIAT_LEVEL_A_MINIO_ARTIFACT_PASSWORD": secrets.token_urlsafe(32),
            "AIAT_LEVEL_A_ROUTER_SECRET": secrets.token_urlsafe(32),
            "AIAT_LEVEL_A_TOOL_SECRET": secrets.token_urlsafe(32),
            "AIAT_LEVEL_A_API_KEY": secrets.token_urlsafe(32),
            "AIAT_LEVEL_A_OPERATOR_KEY": secrets.token_urlsafe(32),
            "AIAT_LEVEL_A_WORKER_KEY": secrets.token_urlsafe(32),
            "AIAT_LEVEL_A_CEO_KEY": secrets.token_urlsafe(32),
            "AIAT_LEVEL_A_OPENCODE_PASSWORD": secrets.token_urlsafe(32),
            "AIAT_LEVEL_A_LITELLM_MASTER_KEY": f"sk-{secrets.token_urlsafe(32)}",
            "AIAT_LEVEL_A_HOST_TOKEN": secrets.token_urlsafe(32),
        }
        env_path = _write_environment(values, tempdir)
        base = _compose_base(project, env_path)
        config = _render_config(base, env)
        if config_only:
            return _report("CONFIG_PASS", compose_file=str(COMPOSE_FILE.relative_to(ROOT)))

        worker_image_preflight = None
        if not stack_only and worker_name == "aiat_deterministic_file_worker":
            worker_image_preflight = _verify_local_deterministic_worker_image(env)
        _require_runsc(env)
        _check_local_base_images(config, env)
        _ensure_profile_images_unused(project, env)
        stack_started = False
        report: dict[str, Any] | None = None
        error: HarnessError | None = None
        try:
            context = _prepare_build_context(tempdir / "build-context" / "mas")
            override = _write_build_override(tempdir / "build-context.override.yml", context)
            base = _compose_base(project, env_path, build_override=override)
            config = _render_config(base, env)
            stack_started = True
            built = _run(base + ["build"], env=env, timeout=1800, check=False)
            if built.returncode:
                raise HarnessError("profile_image_build_failed")
            up = _run(base + ["up", "--detach", "--remove-orphans"], env=env, timeout=240, check=False)
            if up.returncode:
                diagnostic = _safe_compose_start_reason(up.stdout + "\n" + up.stderr)
                raise HarnessError(f"isolated_compose_start_failed:{diagnostic}")
            for service in _COMPLETED_SERVICES:
                _wait_for_container_state(base, service, env, expected="completed")
            for service in _HEALTHY_SERVICES:
                _wait_for_container_state(base, service, env, expected="healthy")
            stack = {
                "project": project,
                "services_healthy": list(_HEALTHY_SERVICES),
                "migrations_completed": True,
                "api_url": "internal://orchestrator-api:8000",
                "sandbox_runtime": "runsc",
                "control_and_sandbox_egress": "blocked_by_internal_networks",
                "worker_host": {
                    "service": "worker-host",
                    "docker_socket_scope": "worker_host_only",
                    "authority": "trusted_host_equivalent_level_a_boundary",
                },
            }
            fixture_calls = _fixture_request_count(base, env)
            stack["provider_fixture_request_count"] = fixture_calls
            if stack_only:
                report = _report("STACK_PASS", stack=stack)
                if fixture_calls != 0:
                    error = HarnessError("provider_fixture_called_during_startup")
            else:
                worker_id = _resolve_worker_id(base, env, worker_name)
                driver_result = _run_driver(base, env, driver, values, worker_id, worker_name)
                fixture_calls = _fixture_request_count(base, env)
                stack["provider_fixture_request_count"] = fixture_calls
                driver_status, driver_reason, worker_artifact_partial = _classify_driver_result(
                    driver_result,
                    worker_name=worker_name,
                    worker_id=worker_id,
                    provider_fixture_calls=fixture_calls,
                )
                report = _report(
                    driver_status,
                    stack=stack,
                    driver=driver_result,
                    selected_worker_id=worker_id,
                    selected_worker_name=worker_name,
                    worker_image_preflight=worker_image_preflight,
                    **({"reason_code": driver_reason} if driver_reason else {}),
                    pending_worker_gate_verified=_pending_worker_gate_verified(driver_result),
                    worker_artifact_completion_blocked_verified=worker_artifact_partial,
                    api_project_cleanup_verified=_api_project_cleanup_verified(driver_result),
                )
                if driver_result["exit_code"] not in {0, 2}:
                    error = HarnessError("api_acceptance_driver_failed")
                elif worker_artifact_partial:
                    error = None
                elif driver_result["exit_code"] == 2 and not _pending_worker_gate_verified(driver_result):
                    error = HarnessError("driver_blocked_outside_pending_worker_gate")
                elif driver_result["exit_code"] == 2 and not _api_project_cleanup_verified(driver_result):
                    error = HarnessError("driver_did_not_verify_api_project_cleanup")
                elif driver_result["exit_code"] == 2 and fixture_calls != 0:
                    error = HarnessError("provider_fixture_called_before_worker_admission")
        except HarnessError as exc:
            error = exc
        finally:
            if stack_started:
                down = _run(
                    base + ["down", "--volumes", "--remove-orphans", "--rmi", "local"],
                    env=env,
                    timeout=180,
                    check=False,
                )
                if down.returncode:
                    cleanup_error = HarnessError("isolated_profile_cleanup_failed")
                    error = error or cleanup_error
                _cleanup_profile_images(project, env)
                try:
                    _verify_profile_cleanup(project, env)
                except HarnessError as cleanup_error:
                    error = error or cleanup_error
            if report is not None:
                report["cleanup"] = (
                    "verified_project_scoped_resources_removed"
                    if error is None
                    else "project_scoped_cleanup_attempted"
                )
        if error is not None:
            if report is None:
                report = _report("FAIL", reason_code=error.code)
            else:
                report["status"] = "FAIL"
                report["reason_code"] = error.code
        if report is None:
            raise HarnessError("profile_did_not_produce_result")
        return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-only", action="store_true", help="Render and validate the isolated Compose security profile without starting containers")
    parser.add_argument("--stack-only", action="store_true", help="Build/start/health-check/clean the isolated stack without dispatching a WorkerRun")
    parser.add_argument(
        "--worker-name",
        choices=SUPPORTED_WORKER_NAMES,
        default="coding_worker",
        help="Canonical worker name to resolve through the authenticated workers API",
    )
    parser.add_argument("--driver", type=Path, default=DEFAULT_DRIVER, help="Repository-local API acceptance driver to copy into the isolated Orchestrator container")
    args = parser.parse_args(argv)
    if args.check_only and args.stack_only:
        parser.error("--check-only and --stack-only cannot be combined")
    try:
        report = run_profile(
            stack_only=args.stack_only,
            config_only=args.check_only,
            driver=args.driver,
            worker_name=args.worker_name,
        )
    except HarnessError as exc:
        report = _report("FAIL", reason_code=exc.code)
        print(json.dumps(report, sort_keys=True))
        return 1
    print(json.dumps(report, sort_keys=True))
    if report.get("status") in {"DRIVER_BLOCKED", "DRIVER_PARTIAL"}:
        return 2
    return 0 if report.get("status") in {"CONFIG_PASS", "STACK_PASS", "DRIVER_PASS"} else 1


if __name__ == "__main__":
    sys.exit(main())
