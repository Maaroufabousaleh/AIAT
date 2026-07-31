#!/usr/bin/env python3
"""Fail-closed v0.16.7 to v0.16.15 Stalwart security upgrade."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import stalwart_secret_migration as migration

CONTAINER = "mas-stalwart-1"
PROJECT = "mas"
SERVICE = "stalwart"
SOURCE_IMAGE = (
    "ghcr.io/stalwartlabs/stalwart:v0.16.7@"
    "sha256:6a8ddaa5728a5e78a8611085069f63414cd43c3a669471785dd41aad1ca16e63"
)
TARGET_IMAGE = (
    "ghcr.io/stalwartlabs/stalwart:v0.16.15@"
    "sha256:4f926193e5dd9ceb1e24ba48160702310381b12e51972c2fb0cc9de020388136"
)


def normalize_repository_digest(image_ref: str) -> str:
    repository, separator, digest = image_ref.rpartition("@")
    if not separator or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError("image reference must contain a sha256 digest")
    slash = repository.rfind("/")
    colon = repository.rfind(":")
    if colon > slash:
        repository = repository[:colon]
    if not repository:
        raise ValueError("image reference must contain a repository")
    return f"{repository}@{digest}"


TARGET_REPOSITORY_DIGEST = normalize_repository_digest(TARGET_IMAGE)
TARGET_PLATFORM = "linux/amd64"
EXPECTED_MOUNTS = {
    (
        "volume",
        "mas_stalwart_local_config",
        "/etc/stalwart",
        True,
    ),
    (
        "volume",
        "mas_stalwart_local_data",
        "/var/lib/stalwart",
        True,
    ),
}
EXPECTED_PORTS = {
    "25/tcp": [{"host_ip": "127.0.0.1", "host_port": "2525"}],
    "8080/tcp": [{"host_ip": "127.0.0.1", "host_port": "18080"}],
}
EXPECTED_NETWORKS = ["mas_internal", "mas_public"]
EXPECTED_RESTART_POLICY = {"Name": "unless-stopped", "MaximumRetryCount": 0}
MANIFEST_NAME = "pre-upgrade-manifest.json"
BACKUP_SUCCESS_NAME = "consistent-backup.json"
CUTOVER_SUCCESS_NAME = "cutover-success.json"
BACKUP_FAILURE_NAME = "backup-failed.json"
PRE_STOP_VALIDATION_NAME = "pre-stop-validation.json"
SOURCE_STOP_INITIATED_NAME = "source-stop-initiated.json"
SOURCE_STOPPED_NAME = "source-stopped.json"
TARGET_RECREATION_INITIATED_NAME = "target-recreation-initiated.json"
TARGET_RECREATION_COMMAND_PASS_NAME = "target-recreation-command-pass.json"
TARGET_RUNNING_NAME = "target-running.json"
VERIFICATION_COMPLETE_NAME = "verification-complete.json"
CUTOVER_FAILURE_NAME = "cutover-failed.json"
LEGACY_ADOPTION_NAME = "legacy-failure-adoption.json"
ATTEMPT_HISTORY_DIR = "attempt-history"
LEGACY_ADOPTION_ARCHIVE_DIR = "legacy-failure-adoption"
ATTEMPT_ARTIFACT_NAMES = (
    PRE_STOP_VALIDATION_NAME,
    SOURCE_STOP_INITIATED_NAME,
    SOURCE_STOPPED_NAME,
    TARGET_RECREATION_INITIATED_NAME,
    TARGET_RECREATION_COMMAND_PASS_NAME,
    TARGET_RUNNING_NAME,
    CUTOVER_FAILURE_NAME,
)
CONFIG_COPY = "etc-stalwart"
DATA_COPY = "var-lib-stalwart"
MAX_DIFFERING_FIELDS = 64
DEFAULT_STOP_TIMEOUT = 45
IGNORED_LABEL_CATEGORIES = (
    "com.docker.compose.*",
    "desktop.docker.io/*",
    "org.opencontainers.image.*",
)

Refused = migration.Refused


class LifecycleError(Refused):
    def __init__(self, message: str, *, code: str, stage: str):
        super().__init__(message)
        self.code = code
        self.stage = stage


class CommandError(Refused):
    def __init__(
        self,
        message: str,
        *,
        code: str,
        command: list[str],
        return_code: int | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.command = command
        self.return_code = return_code


class RecoveryIdentityError(Refused):
    """The recovered source cannot be bound to the governed legacy attempt."""

    code = "source-recovery-identity-unverified"

    def __init__(self, message: str = "source recovery identity is unverified"):
        super().__init__(f"{self.code}: {message}")


class Runner:
    """Run Docker/Compose commands without exposing captured output."""

    def run(
        self,
        args: list[str],
        *,
        env: dict[str, str] | None = None,
        timeout: int = 120,
    ) -> str:
        try:
            completed = subprocess.run(
                args,
                check=False,
                capture_output=True,
                text=True,
                env=env,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise CommandError(
                "command timed out safely",
                code="command-timeout",
                command=args,
            ) from exc
        if completed.returncode != 0:
            code = (
                "compose-command-failure"
                if len(args) > 1 and args[0:2] == ["docker", "compose"]
                else "command-failure"
            )
            raise CommandError(
                "command failed safely",
                code=code,
                command=args,
                return_code=completed.returncode,
            )
        return completed.stdout.strip()

    def json(self, args: list[str]) -> Any:
        raw = self.run(args)
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise CommandError(
                "command returned invalid JSON",
                code="json-parse-failure",
                command=args,
            ) from exc


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def require_root() -> None:
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        raise Refused("run as root so upgrade evidence and backups are root-owned")


def manifest_path(args: argparse.Namespace) -> Path:
    return args.backup_dir / MANIFEST_NAME


def backup_success_path(args: argparse.Namespace) -> Path:
    return args.backup_dir / BACKUP_SUCCESS_NAME


def cutover_success_path(args: argparse.Namespace) -> Path:
    return args.backup_dir / CUTOVER_SUCCESS_NAME


def backup_failure_path(args: argparse.Namespace) -> Path:
    return args.backup_dir / BACKUP_FAILURE_NAME


def cutover_state_path(args: argparse.Namespace, name: str) -> Path:
    return args.backup_dir / name


def record_cutover_phase(
    args: argparse.Namespace,
    name: str,
    value: dict[str, Any],
) -> None:
    path = cutover_state_path(args, name)
    if not path.exists():
        migration.atomic_json(path, value)


def cutover_phase_exists(args: argparse.Namespace, name: str) -> bool:
    return cutover_state_path(args, name).exists()


def attempt_history_path(args: argparse.Namespace) -> Path:
    return args.backup_dir / ATTEMPT_HISTORY_DIR


def legacy_adoption_path(args: argparse.Namespace) -> Path:
    return args.backup_dir / LEGACY_ADOPTION_NAME


def legacy_adoption_archive_path(args: argparse.Namespace) -> Path:
    return attempt_history_path(args) / LEGACY_ADOPTION_ARCHIVE_DIR


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise Refused("upgrade artifact could not be hashed") from exc
    return digest.hexdigest()


def load_protected_artifact(path: Path) -> dict[str, Any]:
    if hasattr(os, "geteuid"):
        try:
            details = path.stat()
        except FileNotFoundError as exc:
            raise Refused(f"invalid or missing upgrade artifact: {path}") from exc
        if details.st_uid != 0 or stat.S_IMODE(details.st_mode) != 0o600:
            raise Refused(f"upgrade artifact must be root-owned mode 0600: {path}")
    return migration.load_json(path)


def require_exact_container_argument(args: argparse.Namespace) -> None:
    if args.container != CONTAINER:
        raise Refused(f"security upgrade is restricted to {CONTAINER}")
    if args.project_name != PROJECT or args.service != SERVICE:
        raise Refused("security upgrade project/service identity is fixed to mas/stalwart")


def env_names(values: list[str]) -> list[str]:
    return sorted({item.split("=", 1)[0] for item in values if "=" in item})


def env_map(values: list[str]) -> dict[str, str]:
    return {
        key: value
        for item in values
        if "=" in item
        for key, value in [item.split("=", 1)]
    }


def normalized_extra_hosts(values: Any) -> list[str]:
    if isinstance(values, dict):
        return sorted(f"{key}:{value}" for key, value in values.items())
    return sorted(str(value) for value in (values or []))


def normalized_labels(values: dict[str, Any]) -> dict[str, str]:
    configured = {
        key: value
        for key, value in values.items()
        if not (
            key.startswith("com.docker.compose.")
            or key.startswith("desktop.docker.io/")
            or key.startswith("org.opencontainers.image.")
        )
    }
    return migration.normalized_label_hashes(configured)


def compose_file_label_values(value: Any) -> list[str]:
    if not value:
        return []
    return sorted(item.strip() for item in str(value).split(",") if item.strip())


def normalized_healthcheck(values: dict[str, Any]) -> dict[str, Any]:
    return {
        "test": values.get("Test") or values.get("test") or [],
        "interval": values.get("Interval") or values.get("interval") or 0,
        "timeout": values.get("Timeout") or values.get("timeout") or 0,
        "start_period": values.get("StartPeriod") or values.get("start_period") or 0,
        "start_interval": (
            values.get("StartInterval") or values.get("start_interval") or 0
        ),
        "retries": values.get("Retries") or values.get("retries") or 0,
    }


def duration_nanoseconds(value: Any) -> int:
    if value in (None, "", 0):
        return 0
    if isinstance(value, int):
        return value
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)(ns|us|ms|s|m|h)", str(value))
    if not match:
        raise Refused("rendered Compose healthcheck contains an unsupported duration")
    multipliers = {
        "ns": 1,
        "us": 1_000,
        "ms": 1_000_000,
        "s": 1_000_000_000,
        "m": 60_000_000_000,
        "h": 3_600_000_000_000,
    }
    return int(float(match.group(1)) * multipliers[match.group(2)])


def compose_healthcheck(
    service: dict[str, Any],
    image_config: dict[str, Any],
) -> dict[str, Any]:
    selected = service.get("healthcheck")
    if selected is None:
        return normalized_healthcheck(image_config.get("Healthcheck") or {})
    if selected.get("disable"):
        return normalized_healthcheck({"Test": ["NONE"]})
    return normalized_healthcheck(
        {
            "Test": selected.get("test") or [],
            "Interval": duration_nanoseconds(selected.get("interval")),
            "Timeout": duration_nanoseconds(selected.get("timeout")),
            "StartPeriod": duration_nanoseconds(selected.get("start_period")),
            "StartInterval": duration_nanoseconds(selected.get("start_interval")),
            "Retries": selected.get("retries") or 0,
        }
    )


def network_aliases(raw: dict[str, Any], *, container_id: str) -> dict[str, list[str]]:
    values: dict[str, list[str]] = {}
    generated = {CONTAINER, SERVICE, container_id, container_id[:12]}
    for name, network in sorted(
        ((raw.get("NetworkSettings") or {}).get("Networks") or {}).items()
    ):
        aliases = {
            str(alias)
            for alias in (network.get("Aliases") or [])
            if alias and str(alias) not in generated
        }
        values[name] = sorted(aliases)
    return values


def live_semantics(raw: dict[str, Any]) -> dict[str, Any]:
    config = raw.get("Config") or {}
    host = raw.get("HostConfig") or {}
    labels = config.get("Labels") or {}
    container_id = str(raw.get("Id") or "")
    return {
        "image": config.get("Image") or "",
        "container_name": str(raw.get("Name") or "").lstrip("/"),
        "compose_project": labels.get("com.docker.compose.project") or "",
        "compose_service": labels.get("com.docker.compose.service") or "",
        "compose_working_directory": labels.get(
            "com.docker.compose.project.working_dir"
        )
        or "",
        "compose_config_files": compose_file_label_values(
            labels.get("com.docker.compose.project.config_files")
        ),
        "running": bool((raw.get("State") or {}).get("Running")),
        "health": ((raw.get("State") or {}).get("Health") or {}).get("Status")
        or "none",
        "command": config.get("Cmd"),
        "entrypoint": config.get("Entrypoint"),
        "working_directory": config.get("WorkingDir") or "",
        "user": config.get("User") or "",
        "environment_names": env_names(config.get("Env") or []),
        "resend_secret_present": migration.SECRET_NAME
        in env_names(config.get("Env") or []),
        "mounts": [
            {
                "type": item["type"],
                "name": item["name"],
                "destination": item["destination"],
                "rw": item["rw"],
                "propagation": item["propagation"],
            }
            for item in migration.normalized_mounts(raw)
        ],
        "ports": migration.normalized_ports(raw),
        "networks": network_aliases(raw, container_id=container_id),
        "restart_policy": host.get("RestartPolicy") or {},
        "healthcheck": normalized_healthcheck(config.get("Healthcheck") or {}),
        "labels": normalized_labels(labels),
        "security_opt": sorted(host.get("SecurityOpt") or []),
        "cap_add": sorted(host.get("CapAdd") or []),
        "cap_drop": sorted(host.get("CapDrop") or []),
        "privileged": bool(host.get("Privileged")),
        "read_only_rootfs": bool(host.get("ReadonlyRootfs")),
        "dns": sorted(host.get("Dns") or []),
        "dns_options": sorted(host.get("DnsOptions") or []),
        "dns_search": sorted(host.get("DnsSearch") or []),
        "extra_hosts": normalized_extra_hosts(host.get("ExtraHosts")),
        "memory": host.get("Memory") or 0,
        "nano_cpus": host.get("NanoCpus") or 0,
    }


def parse_compose_json(
    runner: Runner,
    args: argparse.Namespace,
    *,
    include_override: bool = False,
) -> dict[str, Any]:
    raw = runner.run(
        migration.compose_command(args, include_override)
        + ["config", "--format", "json", "--no-env-resolution"],
        env=migration.compose_environment(args),
    )
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise Refused("Compose source render did not return valid JSON") from exc
    if not isinstance(value, dict):
        raise Refused("Compose source render did not return an object")
    return value


def image_config(runner: Runner, image: str) -> dict[str, Any]:
    values = runner.json(["docker", "image", "inspect", image])
    if not isinstance(values, list) or len(values) != 1:
        raise Refused("approved source image configuration is unavailable")
    return values[0].get("Config") or {}


def compose_volume_name(
    compose: dict[str, Any],
    source: str,
    *,
    project: str,
) -> str:
    definition = (compose.get("volumes") or {}).get(source) or {}
    return definition.get("name") or f"{project}_{source}"


def compose_network_name(
    compose: dict[str, Any],
    source: str,
    *,
    project: str,
) -> str:
    definition = (compose.get("networks") or {}).get(source) or {}
    return definition.get("name") or f"{project}_{source}"


def rendered_semantics(
    runner: Runner,
    args: argparse.Namespace,
    compose: dict[str, Any],
    *,
    expected_image: str = SOURCE_IMAGE,
    include_override: bool = False,
) -> dict[str, Any]:
    service = (compose.get("services") or {}).get(args.service)
    if not isinstance(service, dict):
        raise Refused("canonical Compose source does not define stalwart")
    image = str(service.get("image") or "")
    if image != expected_image:
        raise Refused("rendered Compose image is not the approved lifecycle image")
    image_values = image_config(runner, image)
    environment_names = set(env_names(image_values.get("Env") or []))
    environment_names.update((service.get("environment") or {}).keys())
    environment_names.add(migration.SECRET_NAME)
    mounts = []
    for item in service.get("volumes") or []:
        mount_type = item.get("type") or "volume"
        source = str(item.get("source") or "")
        mounts.append(
            {
                "type": mount_type,
                "name": (
                    compose_volume_name(compose, source, project=args.project_name)
                    if mount_type == "volume"
                    else ""
                ),
                "destination": item.get("target") or "",
                "rw": not bool(item.get("read_only")),
                "propagation": "",
            }
        )
    ports: dict[str, list[dict[str, str]]] = {}
    for item in service.get("ports") or []:
        key = f"{item.get('target')}/{item.get('protocol') or 'tcp'}"
        ports.setdefault(key, []).append(
            {
                "host_ip": item.get("host_ip") or "",
                "host_port": str(item.get("published") or ""),
            }
        )
    networks: dict[str, list[str]] = {}
    for source, settings in (service.get("networks") or {}).items():
        name = compose_network_name(compose, source, project=args.project_name)
        networks[name] = sorted((settings or {}).get("aliases") or [])
    restart = service.get("restart") or "no"
    restart_policy = {
        "Name": restart,
        "MaximumRetryCount": 0,
    }
    if restart.startswith("on-failure:"):
        restart_policy = {
            "Name": "on-failure",
            "MaximumRetryCount": int(restart.split(":", 1)[1]),
        }
    command = service.get("command")
    if command is None:
        command = image_values.get("Cmd")
    entrypoint = service.get("entrypoint")
    if entrypoint is None:
        entrypoint = image_values.get("Entrypoint")
    labels = dict(service.get("labels") or {})
    return {
        "image": image,
        "container_name": CONTAINER,
        "compose_project": args.project_name,
        "compose_service": args.service,
        "compose_working_directory": str(args.project_directory.resolve()),
        "compose_config_files": sorted(
            str(path.resolve())
            for path in [
                *args.compose_file,
                *( [args.override_file] if include_override else []),
            ]
        ),
        "running": True,
        "health": "healthy",
        "command": command,
        "entrypoint": entrypoint,
        "working_directory": (
            service.get("working_dir") or image_values.get("WorkingDir") or ""
        ),
        "user": service.get("user") or image_values.get("User") or "",
        "environment_names": sorted(environment_names),
        "resend_secret_present": True,
        "mounts": sorted(mounts, key=lambda item: item["destination"]),
        "ports": {
            key: sorted(value, key=lambda item: (item["host_ip"], item["host_port"]))
            for key, value in sorted(ports.items())
        },
        "networks": dict(sorted(networks.items())),
        "restart_policy": restart_policy,
        "healthcheck": compose_healthcheck(service, image_values),
        "labels": normalized_labels(labels),
        "security_opt": sorted(service.get("security_opt") or []),
        "cap_add": sorted(service.get("cap_add") or []),
        "cap_drop": sorted(service.get("cap_drop") or []),
        "privileged": bool(service.get("privileged")),
        "read_only_rootfs": bool(service.get("read_only")),
        "dns": sorted(service.get("dns") or []),
        "dns_options": sorted(service.get("dns_opt") or []),
        "dns_search": sorted(service.get("dns_search") or []),
        "extra_hosts": normalized_extra_hosts(service.get("extra_hosts")),
        "memory": int(service.get("mem_limit") or 0),
        "nano_cpus": int(float(service.get("cpus") or 0) * 1_000_000_000),
    }


def source_definition_differences(
    original: dict[str, Any],
    recovered: dict[str, Any],
) -> list[str]:
    """Return canonical source-definition fields that changed."""
    original_definition = migration.normalized_definition(original["definition"])
    recovered_definition = migration.normalized_definition(recovered["definition"])
    return sorted(
        field
        for field in set(original_definition) | set(recovered_definition)
        if original_definition.get(field) != recovered_definition.get(field)
    )


def is_expected_docker_generated_hostname_change(
    original_snapshot: dict[str, Any],
    recovered_snapshot: dict[str, Any],
    rendered_service: dict[str, Any],
) -> bool:
    """Allow only Docker's ID-derived hostname change after source recovery."""
    if "hostname" in rendered_service:
        return False
    original_id = str(original_snapshot.get("container_id") or "")
    recovered_id = str(recovered_snapshot.get("container_id") or "")
    if (
        original_id == recovered_id
        or not re.fullmatch(r"[0-9a-f]{12,64}", original_id)
        or not re.fullmatch(r"[0-9a-f]{12,64}", recovered_id)
    ):
        return False
    original_hostname = str(
        original_snapshot.get("definition", {}).get("hostname") or ""
    )
    recovered_hostname = str(
        recovered_snapshot.get("definition", {}).get("hostname") or ""
    )
    return (
        bool(original_hostname)
        and bool(recovered_hostname)
        and re.fullmatch(r"[0-9a-f]{12}", original_hostname) is not None
        and re.fullmatch(r"[0-9a-f]{12}", recovered_hostname) is not None
        and original_hostname == original_id[:12]
        and recovered_hostname == recovered_id[:12]
        and original_hostname != recovered_hostname
    )


def source_hostname_metadata(
    original_snapshot: dict[str, Any],
    recovered_snapshot: dict[str, Any],
    *,
    hostname_regenerated: bool,
) -> dict[str, Any]:
    original_hostname = str(
        original_snapshot.get("definition", {}).get("hostname") or ""
    )
    recovered_hostname = str(
        recovered_snapshot.get("definition", {}).get("hostname") or ""
    )
    original_id = str(original_snapshot.get("container_id") or "")
    recovered_id = str(recovered_snapshot.get("container_id") or "")
    generated_for_both = (
        re.fullmatch(r"[0-9a-f]{12,64}", original_id) is not None
        and re.fullmatch(r"[0-9a-f]{12,64}", recovered_id) is not None
        and original_hostname == original_id[:12]
        and recovered_hostname == recovered_id[:12]
    )
    return {
        "original_source_hostname": original_hostname,
        "recovered_source_hostname": recovered_hostname,
        "source_hostname_regenerated": hostname_regenerated,
        "hostname_source": "DOCKER_CONTAINER_ID" if generated_for_both else "UNCHANGED",
    }


def secret_override_matches(service: dict[str, Any], args: argparse.Namespace) -> bool:
    env_files = service.get("env_file") or []
    if len(env_files) != 1 or not isinstance(env_files[0], dict):
        return False
    configured = env_files[0]
    try:
        path_matches = Path(str(configured.get("path") or "")).resolve() == (
            args.secret_file.resolve()
        )
    except OSError:
        return False
    return path_matches and configured.get("format") == "raw"


def environment_values_match(
    raw: dict[str, Any],
    service: dict[str, Any],
    image_values: dict[str, Any],
) -> bool:
    live = env_map((raw.get("Config") or {}).get("Env") or [])
    live.pop(migration.SECRET_NAME, None)
    expected = env_map(image_values.get("Env") or [])
    expected.update(
        {
            str(key): "" if value is None else str(value)
            for key, value in (service.get("environment") or {}).items()
        }
    )
    expected.pop(migration.SECRET_NAME, None)
    return live == expected


def require_compose_container_identity(
    runner: Runner,
    args: argparse.Namespace,
    snapshot: dict[str, Any],
    *,
    include_override: bool = False,
) -> None:
    if snapshot["compose"]["project"] != args.project_name:
        raise Refused("container Compose project does not match")
    if snapshot["compose"]["service"] != args.service:
        raise Refused("container Compose service does not match")
    current_id = runner.run(
        migration.compose_command(args, include_override) + ["ps", "-q", args.service],
        env=migration.compose_environment(args),
    )
    if current_id != snapshot["container_id"]:
        raise Refused("Compose does not resolve to the inspected Stalwart container")


def repository_drift(
    runner: Runner,
    args: argparse.Namespace,
    *,
    created_at: str,
) -> dict[str, Any]:
    files = [str(path) for path in args.compose_file]
    result = {
        "working_tree_changed": False,
        "committed_after_container_creation": False,
        "history_checked": False,
    }
    try:
        root = runner.run(
            ["git", "-C", str(args.project_directory), "rev-parse", "--show-toplevel"]
        )
        status = runner.run(["git", "-C", root, "status", "--porcelain", "--", *files])
        result["working_tree_changed"] = bool(status)
        history = runner.run(
            [
                "git",
                "-C",
                root,
                "log",
                "-1",
                "--format=%cI",
                "--",
                *files,
            ]
        )
        if history and created_at:
            committed = datetime.fromisoformat(history.replace("Z", "+00:00"))
            created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            result["committed_after_container_creation"] = committed > created
        result["history_checked"] = True
    except (Refused, ValueError):
        pass
    return result


def compare_service_semantics(
    runner: Runner,
    args: argparse.Namespace,
    raw: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    expected_image: str,
    include_override: bool,
    classify_repository_drift: bool,
) -> dict[str, Any]:
    compose = parse_compose_json(runner, args, include_override=include_override)
    live = live_semantics(raw)
    rendered = rendered_semantics(
        runner,
        args,
        compose,
        expected_image=expected_image,
        include_override=include_override,
    )
    differing = sorted(
        field for field in rendered if live.get(field) != rendered.get(field)
    )
    service = (compose.get("services") or {}).get(args.service) or {}
    source_image_config = image_config(runner, expected_image)
    if not environment_values_match(raw, service, source_image_config):
        differing.append("environment_values")
    if not secret_override_matches(service, args):
        differing.append("secret_override")
    differing = sorted(set(differing))
    if len(differing) > MAX_DIFFERING_FIELDS:
        differing = differing[:MAX_DIFFERING_FIELDS]
    rendered_hash = migration.compose_service_hash(
        runner,
        args,
        include_override=include_override,
    )
    live_hash = snapshot["compose"]["config_hash"]
    hash_match = live_hash == rendered_hash
    provenance = (
        repository_drift(
            runner,
            args,
            created_at=str(raw.get("Created") or ""),
        )
        if classify_repository_drift
        else {}
    )
    if differing:
        drift_class = "MATERIAL_DRIFT"
    elif hash_match:
        drift_class = "NONE"
    elif (
        provenance.get("working_tree_changed", False)
        or provenance.get("committed_after_container_creation", False)
    ):
        drift_class = "REPOSITORY_CHANGE"
    else:
        drift_class = "COMPOSE_METADATA"
    return {
        "source_semantic_match": not differing,
        "config_hash_match": hash_match,
        "config_hash_drift_class": drift_class,
        "differing_fields": differing,
        "live_config_hash": live_hash,
        "rendered_source_config_hash": rendered_hash,
        "repository_provenance": provenance,
        "source_files": [str(path.resolve()) for path in args.compose_file],
        "ignored_label_categories": list(IGNORED_LABEL_CATEGORIES),
        "target_override_in_source_comparison": False,
    }


def compare_source_semantics(
    runner: Runner,
    args: argparse.Namespace,
    raw: dict[str, Any],
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    return compare_service_semantics(
        runner,
        args,
        raw,
        snapshot,
        expected_image=SOURCE_IMAGE,
        include_override=False,
        classify_repository_drift=True,
    )


def compare_target_semantics(
    runner: Runner,
    args: argparse.Namespace,
    raw: dict[str, Any],
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    return compare_service_semantics(
        runner,
        args,
        raw,
        snapshot,
        expected_image=TARGET_IMAGE,
        include_override=True,
        classify_repository_drift=False,
    )


def print_source_report(report: dict[str, Any]) -> None:
    print(
        "SOURCE_SEMANTIC_MATCH="
        + ("PASS" if report["source_semantic_match"] else "FAIL")
    )
    print("CONFIG_HASH_MATCH=" + ("PASS" if report["config_hash_match"] else "FAIL"))
    print(f"CONFIG_HASH_DRIFT_CLASS={report['config_hash_drift_class']}")
    print(
        "IGNORED_LABEL_CATEGORIES="
        + ",".join(report.get("ignored_label_categories", IGNORED_LABEL_CATEGORIES))
    )
    for field in report["differing_fields"]:
        print(f"DIFFERING_FIELD={field}")
    if not report["differing_fields"]:
        print("DIFFERING_FIELD=NONE")
    print("SECRET_VALUE_OR_FINGERPRINT_OUTPUT=NONE")
    print("LIVE_MUTATION=NOT_PERFORMED")


def print_target_report(report: dict[str, str]) -> None:
    print("TARGET_IMAGE_LOCAL=PASS")
    print("TARGET_REPOSITORY_MATCH=" + report["target_repository_match"])
    print("TARGET_DIGEST_MATCH=" + report["target_digest_match"])
    print("TARGET_PLATFORM=" + report["target_platform"])


def validate_exact_source(snapshot: dict[str, Any]) -> None:
    migration.validate_snapshot(
        snapshot,
        persistent_target="/var/lib/stalwart",
        require_secret=True,
    )
    definition = snapshot["definition"]
    if definition["name"] != CONTAINER:
        raise Refused(f"live container must be exactly {CONTAINER}")
    if definition["image_ref"] != SOURCE_IMAGE:
        raise Refused("running Stalwart image is not the approved pinned v0.16.7 digest")
    mounts = {
        (
            item["type"],
            item["name"],
            item["destination"],
            item["rw"],
        )
        for item in definition["mounts"]
    }
    if mounts != EXPECTED_MOUNTS:
        raise Refused("Stalwart volume names or destinations drifted")
    if definition["ports"] != EXPECTED_PORTS:
        raise Refused("Stalwart published ports drifted")
    if definition["networks"] != EXPECTED_NETWORKS:
        raise Refused("Stalwart networks drifted")
    if definition["restart_policy"] != EXPECTED_RESTART_POLICY:
        raise Refused("Stalwart restart policy drifted")
    if definition["healthcheck"] == migration.canonical_hash({}):
        raise Refused("Stalwart healthcheck is missing")
    compose = snapshot.get("compose", {})
    if (
        compose.get("project") != PROJECT
        or compose.get("service") != SERVICE
        or not compose.get("config_hash")
    ):
        raise Refused("Stalwart Compose project/service identity drifted")


def target_image_validation(runner: Runner) -> tuple[str, dict[str, str]]:
    values = runner.json(["docker", "image", "inspect", TARGET_IMAGE])
    if not isinstance(values, list) or len(values) != 1:
        raise Refused("approved v0.16.15 target digest is not locally present")
    raw = values[0]
    image_id = str(raw.get("Id") or "")
    repo_metadata = set(raw.get("RepoDigests") or []) | set(raw.get("RepoTags") or [])
    repository_match = TARGET_REPOSITORY_DIGEST in repo_metadata
    platform = f"{raw.get('Os') or ''}/{raw.get('Architecture') or ''}"
    digest_match = repository_match
    if not image_id.startswith("sha256:"):
        raise Refused("local v0.16.15 image has no immutable image ID")
    if not repository_match:
        raise Refused("local v0.16.15 image does not match the approved repository@digest")
    if platform != TARGET_PLATFORM:
        raise Refused("local v0.16.15 image platform is not linux/amd64")
    return image_id, {
        "target_repository_match": "PASS" if repository_match else "FAIL",
        "target_digest_match": "PASS" if digest_match else "FAIL",
        "target_platform": platform,
    }


def validate_target_image_local(runner: Runner) -> str:
    image_id, _report = target_image_validation(runner)
    return image_id


def analyze_source_runtime(
    runner: Runner,
    args: argparse.Namespace,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    values = runner.json(["docker", "inspect", args.container])
    if not isinstance(values, list) or len(values) != 1:
        raise Refused("Docker did not return exactly one Stalwart container")
    raw = values[0]
    snapshot = migration.snapshot_from_inspect(raw)
    migration.prepare_compose_environment(runner, args)
    report = compare_source_semantics(runner, args, raw, snapshot)
    return raw, snapshot, report


def inspect_source_runtime(
    runner: Runner,
    args: argparse.Namespace,
) -> tuple[dict[str, Any], dict[str, Any]]:
    _raw, snapshot, report = analyze_source_runtime(runner, args)
    if not report["source_semantic_match"]:
        return snapshot, report
    validate_exact_source(snapshot)
    migration.validate_mount_tracking(runner, snapshot, project=args.project_name)
    require_compose_container_identity(runner, args, snapshot)
    secret = migration.protected_secret(args.secret_file)
    try:
        migration.secret_matches_container(runner, args.container, secret)
    finally:
        secret = ""
    return snapshot, report


def prepare_runtime(runner: Runner, args: argparse.Namespace) -> dict[str, Any]:
    snapshot, report = inspect_source_runtime(runner, args)
    if not report["source_semantic_match"]:
        print_source_report(report)
        raise Refused("running container has material source-definition drift")
    return {"snapshot": snapshot, "source_comparison": report}


def build_manifest(
    runner: Runner,
    args: argparse.Namespace,
    snapshot: dict[str, Any],
    source_comparison: dict[str, Any],
) -> dict[str, Any]:
    target_image_id, target_validation = target_image_validation(runner)
    target_compose_hash = migration.compose_service_hash(
        runner,
        args,
        include_override=True,
    )
    if target_compose_hash == source_comparison["rendered_source_config_hash"]:
        raise Refused("v0.16.15 override did not change the Compose service definition")
    return {
        "schema": 1,
        "kind": "stalwart-v0.16.15-security-upgrade",
        "container": CONTAINER,
        "project": PROJECT,
        "service": SERVICE,
        "source": snapshot,
        "source_comparison": source_comparison,
        "source_image": SOURCE_IMAGE,
        "target_image": TARGET_IMAGE,
        "target_image_id": target_image_id,
        "target_image_validation": target_validation,
        "target_compose_hash": target_compose_hash,
        "compose_file_hashes": migration.compose_file_hashes(args),
        "sanitization": {
            "environment_values_stored": False,
            "secret_value_stored": False,
            "secret_fingerprint_stored": False,
            "label_values_stored": False,
        },
    }


def inspect_action(runner: Runner, args: argparse.Namespace) -> None:
    require_root()
    require_exact_container_argument(args)
    runtime = prepare_runtime(runner, args)
    snapshot = runtime["snapshot"]
    source_comparison = runtime["source_comparison"]
    manifest = build_manifest(runner, args, snapshot, source_comparison)
    path = manifest_path(args)
    if path.exists():
        if load_protected_artifact(path) != manifest:
            raise Refused("existing pre-upgrade manifest is stale or live state drifted")
        print("PRE_UPGRADE_INSPECTION=PASS")
        print("INSPECTION_RESUME=VERIFIED")
    else:
        migration.atomic_json(path, manifest)
        print("PRE_UPGRADE_INSPECTION=PASS")
        print("INSPECTION_RESUME=NEW")
    print(f"MANIFEST={path}")
    print("SOURCE_IMAGE=APPROVED_V0.16.7")
    target_validation = manifest["target_image_validation"]
    print_target_report(target_validation)
    print("RESEND_API_KEY_PRESENT=PASS")
    print("RESEND_API_KEY_SOURCE_MATCH=PASS")
    print_source_report(source_comparison)
    print("CONFIG_HASH_DRIFT_RECORDED=PASS")
    print("SECRET_VALUE_OR_FINGERPRINT_OUTPUT=NONE")
    print("LIVE_MUTATION=NOT_PERFORMED")


def diagnose_action(runner: Runner, args: argparse.Namespace) -> None:
    require_root()
    require_exact_container_argument(args)
    _target_id, target_report = target_image_validation(runner)
    _raw, snapshot, report = analyze_source_runtime(runner, args)
    auxiliary_differences: list[str] = []
    try:
        validate_exact_source(snapshot)
    except Refused:
        auxiliary_differences.append("source_invariant")
    try:
        migration.validate_mount_tracking(
            runner,
            snapshot,
            project=args.project_name,
        )
    except Refused:
        auxiliary_differences.append("mount_tracking")
    try:
        require_compose_container_identity(runner, args, snapshot)
    except Refused:
        auxiliary_differences.append("compose_resolution")
    try:
        secret = migration.protected_secret(args.secret_file)
        try:
            migration.secret_matches_container(runner, args.container, secret)
        finally:
            secret = ""
    except Refused:
        auxiliary_differences.append("resend_secret_source")
    if auxiliary_differences:
        report["source_semantic_match"] = False
        report["config_hash_drift_class"] = "MATERIAL_DRIFT"
        report["differing_fields"] = sorted(
            set([*report["differing_fields"], *auxiliary_differences])
        )[:MAX_DIFFERING_FIELDS]
    print_source_report(report)
    print_target_report(target_report)
    if not report["source_semantic_match"]:
        raise Refused("running container has material source-definition drift")


def read_manifest(args: argparse.Namespace) -> dict[str, Any]:
    value = load_protected_artifact(manifest_path(args))
    if (
        value.get("schema") != 1
        or value.get("kind") != "stalwart-v0.16.15-security-upgrade"
        or value.get("container") != CONTAINER
        or value.get("source_image") != SOURCE_IMAGE
        or value.get("target_image") != TARGET_IMAGE
    ):
        raise Refused("pre-upgrade manifest is invalid")
    if value.get("compose_file_hashes") != migration.compose_file_hashes(args):
        raise Refused("Compose sources changed after pre-upgrade inspection")
    return value


def tree_stats(path: Path) -> dict[str, int]:
    files = 0
    bytes_total = 0
    for item in path.rglob("*"):
        if item.is_file():
            files += 1
            bytes_total += item.stat().st_size
    if files == 0:
        raise Refused(f"consistent backup tree is empty: {path.name}")
    return {"files": files, "bytes": bytes_total}


def wait_for_source_healthy(runner: Runner, args: argparse.Namespace) -> dict[str, Any]:
    snapshot = migration.wait_for_healthy(runner, args.container)
    validate_exact_source(snapshot)
    return snapshot


def inspect_stopped_source(runner: Runner, args: argparse.Namespace) -> dict[str, Any]:
    values = runner.json(["docker", "inspect", args.container])
    if not isinstance(values, list) or len(values) != 1:
        raise Refused("stopped source container could not be inspected")
    raw = values[0]
    state = raw.get("State") or {}
    if state.get("Running"):
        raise Refused("source container did not stop")
    exit_code = int(state.get("ExitCode") or 0)
    return {
        "container_id": raw.get("Id") or "",
        "exit_code": exit_code,
        "oom_killed": bool(state.get("OOMKilled")),
        "sigkill_required": exit_code == 137,
        "graceful_stop": exit_code != 137,
    }


def validate_recovered_source(
    runner: Runner,
    args: argparse.Namespace,
) -> dict[str, Any]:
    _raw, snapshot, report = analyze_source_runtime(runner, args)
    if not report["source_semantic_match"]:
        raise Refused("recovered v0.16.7 source has material definition drift")
    validate_exact_source(snapshot)
    migration.validate_mount_tracking(runner, snapshot, project=args.project_name)
    require_compose_container_identity(runner, args, snapshot)
    secret = migration.protected_secret(args.secret_file)
    try:
        migration.secret_matches_container(runner, args.container, secret)
    finally:
        secret = ""
    return snapshot


def recover_source(
    runner: Runner,
    args: argparse.Namespace,
    *,
    target_recreation_started: bool,
) -> dict[str, Any]:
    recovery_method = "docker-start"
    try:
        if target_recreation_started:
            raise Refused("target recreation began; use the source Compose rollback")
        runner.run(["docker", "start", args.container], timeout=args.stop_timeout + 30)
    except Refused:
        recovery_method = "compose-source-recreate"
        runner.run(
            migration.compose_command(args, False)
            + [
                "up",
                "-d",
                "--no-deps",
                "--force-recreate",
                "--no-build",
                "--pull",
                "never",
                args.service,
            ],
            env=migration.compose_environment(args),
            timeout=300,
        )
    recovered = wait_for_source_healthy(runner, args)
    recovered = validate_recovered_source(runner, args)
    return {
        "source_auto_recovery": "PASS",
        "recovery_method": recovery_method,
        "container_id": recovered["container_id"],
        "completed_at": utc_now(),
    }


FAILURE_MESSAGES = {
    "command-timeout": "command timed out",
    "compose-command-failure": "Compose command failed",
    "command-failure": "command failed",
    "json-parse-failure": "command returned invalid JSON",
    "health-timeout": "target health wait timed out",
    "target-image-mismatch": "target image validation failed",
    "target-container-id-mismatch": "target container identity validation failed",
    "target-semantic-mismatch": "target semantic validation failed",
    "compose-resolution-mismatch": "target Compose identity validation failed",
    "target-inspect-failure": "target inspection failed",
    "secret-verification-failure": "target secret verification failed",
    "source-stop-sigkill": "source stop required SIGKILL",
    "legacy-failure-artifact": "legacy failed-attempt artifact",
    "operation-refused": "upgrade operation refused",
}
SAFE_EXCEPTION_TYPES = {
    "CommandError",
    "LifecycleError",
    "Refused",
    "TimeoutError",
    "OSError",
    "Exception",
}


def safe_command_scope(exc: Exception) -> str:
    command = getattr(exc, "command", None)
    if not isinstance(command, list) or not command:
        return "NOT_AVAILABLE"
    executable = str(command[0])
    subcommand = str(command[1]) if len(command) > 1 else ""
    if executable == "docker" and subcommand in {
        "compose",
        "image",
        "inspect",
        "start",
        "stop",
    }:
        return f"docker {subcommand}"
    if executable == "docker":
        return "docker"
    return executable


def target_failure_evidence(
    runner: Runner,
    args: argparse.Namespace,
) -> dict[str, Any]:
    """Collect only safe target lifecycle facts before recovery begins."""
    evidence: dict[str, Any] = {
        "target_container_created": False,
        "target_became_healthy": False,
        "target_container_id": "",
    }
    try:
        values = runner.json(["docker", "inspect", args.container])
        if not isinstance(values, list) or len(values) != 1:
            return evidence
        raw = values[0]
        config = raw.get("Config") or {}
        if config.get("Image") != TARGET_IMAGE:
            return evidence
        target_id = str(raw.get("Id") or "")
        if re.fullmatch(r"[0-9a-f]{12,64}", target_id):
            evidence["target_container_id"] = target_id
        state = raw.get("State") or {}
        evidence["target_container_created"] = True
        evidence["target_became_healthy"] = bool(
            state.get("Running")
            and (state.get("Health") or {}).get("Status") == "healthy"
        )
    except Exception:
        pass
    return evidence


def failure_details(
    exc: Exception,
    *,
    last_completed_phase: str,
    target_recreation_started: bool,
    target_evidence: dict[str, Any],
) -> dict[str, Any]:
    stage = getattr(exc, "stage", None)
    code = getattr(exc, "code", None)
    if not isinstance(stage, str) or not re.fullmatch(r"[A-Z0-9_]+", stage):
        stage = (
            "TARGET_RECREATION_COMMAND"
            if target_recreation_started and last_completed_phase == "SOURCE_STOPPED"
            else last_completed_phase
        )
    if not isinstance(code, str) or not re.fullmatch(r"[a-z0-9-]+", code):
        code = "source-stop-sigkill" if "SIGKILL" in str(exc) else "operation-refused"
    message = FAILURE_MESSAGES.get(code, FAILURE_MESSAGES["operation-refused"])
    exception_type = type(exc).__name__
    if exception_type not in SAFE_EXCEPTION_TYPES:
        exception_type = "Exception"
    return {
        "failure_stage": stage,
        "exception_type": exception_type,
        "error_code": code,
        "sanitized_message": message,
        "command_executable_subcommand": safe_command_scope(exc),
        "return_code": (
            getattr(exc, "return_code", None)
            if isinstance(getattr(exc, "return_code", None), int)
            else None
        ),
        **target_evidence,
    }


def record_cutover_failure(
    args: argparse.Namespace,
    *,
    last_completed_phase: str,
    mutation: bool,
    recovery: dict[str, Any] | None,
    exc: Exception,
    target_recreation_started: bool,
    target_evidence: dict[str, Any],
) -> None:
    details = failure_details(
        exc,
        last_completed_phase=last_completed_phase,
        target_recreation_started=target_recreation_started,
        target_evidence=target_evidence,
    )
    record_cutover_phase(
        args,
        CUTOVER_FAILURE_NAME,
        {
            "schema": 1,
            "container": CONTAINER,
            "last_completed_phase": last_completed_phase,
            "live_mutation": "PERFORMED" if mutation else "NOT_PERFORMED",
            "source_auto_recovery": (
                (recovery or {}).get("source_auto_recovery") or "NOT_ATTEMPTED"
            ),
            "recovery_method": (recovery or {}).get("recovery_method") or "",
            "failed_at": utc_now(),
            "secret_or_fingerprint_stored": False,
            "volumes_deleted_or_recreated": False,
            **details,
        },
    )


def backup_integrity_action(runner: Runner, args: argparse.Namespace) -> None:
    require_root()
    require_exact_container_argument(args)
    manifest = read_manifest(args)
    read_backup_success(args, manifest)
    print("BACKUP_INTEGRITY=PASS")
    print("LIVE_MUTATION=NOT_PERFORMED")
    print("DOCKER_VOLUME_MUTATION=NONE")
    print("SECRET_VALUE_OR_FINGERPRINT_OUTPUT=NONE")


def read_cutover_failure(args: argparse.Namespace) -> dict[str, Any]:
    value = load_protected_artifact(cutover_state_path(args, CUTOVER_FAILURE_NAME))
    base_required = {
        "schema",
        "container",
        "last_completed_phase",
        "source_auto_recovery",
        "volumes_deleted_or_recreated",
    }
    if not base_required.issubset(value):
        raise Refused("cutover failure artifact is incomplete")
    if value.get("schema") != 1 or value.get("container") != CONTAINER:
        raise Refused("cutover failure artifact is invalid")
    if value.get("volumes_deleted_or_recreated") is not False:
        raise Refused("cutover failure artifact records forbidden volume mutation")
    if value.get("source_auto_recovery") not in {"PASS", "FAIL", "NOT_ATTEMPTED"}:
        raise Refused("cutover failure artifact has invalid recovery state")
    required = {
        "failure_stage",
        "exception_type",
        "error_code",
        "sanitized_message",
        "command_executable_subcommand",
        "return_code",
        "target_container_created",
        "target_became_healthy",
        "source_auto_recovery",
        "volumes_deleted_or_recreated",
    }
    if not required.issubset(value):
        # Artifacts written by the immediately preceding lifecycle version did
        # not yet carry bounded failure classification. Keep target state
        # explicitly unknown; only adopt-legacy-failure may establish target
        # absence through an independent Docker read-only check.
        value = {
            **value,
            "failure_stage": value.get("last_completed_phase") or "UNKNOWN",
            "exception_type": "LegacyFailureArtifact",
            "error_code": "legacy-failure-artifact",
            "sanitized_message": FAILURE_MESSAGES["legacy-failure-artifact"],
            "command_executable_subcommand": "NOT_AVAILABLE",
            "return_code": None,
            "target_container_created": "UNKNOWN",
            "target_became_healthy": "UNKNOWN",
            "target_container_id": "",
        }
    if value.get("sanitized_message") not in FAILURE_MESSAGES.values():
        raise Refused("cutover failure artifact contains an unapproved message")
    return value


LEGACY_FAILURE_PHASES = {
    "SOURCE_STOPPED",
    "TARGET_RECREATION",
    "TARGET_RECREATION_COMMAND_PASS",
}
NEW_FAILURE_FIELDS = {
    "failure_stage",
    "exception_type",
    "error_code",
    "sanitized_message",
    "command_executable_subcommand",
    "return_code",
    "target_container_created",
    "target_became_healthy",
    "target_container_id",
}
LEGACY_FAILURE_ALLOWED_FIELDS = {
    "schema",
    "container",
    "last_completed_phase",
    "live_mutation",
    "source_auto_recovery",
    "recovery_method",
    "failed_at",
    "secret_or_fingerprint_stored",
    "volumes_deleted_or_recreated",
}


def read_legacy_failure(args: argparse.Namespace) -> dict[str, Any]:
    value = load_protected_artifact(cutover_state_path(args, CUTOVER_FAILURE_NAME))
    if NEW_FAILURE_FIELDS.issubset(value):
        raise Refused("cutover failure artifact is already normalized; adoption is not required")
    if (
        not set(value).issubset(LEGACY_FAILURE_ALLOWED_FIELDS)
        or
        value.get("schema") != 1
        or value.get("container") != CONTAINER
        or value.get("live_mutation") != "PERFORMED"
        or value.get("source_auto_recovery") != "PASS"
        or value.get("volumes_deleted_or_recreated") is not False
        or value.get("secret_or_fingerprint_stored") is not False
        or value.get("last_completed_phase") not in LEGACY_FAILURE_PHASES
        or not isinstance(value.get("recovery_method"), str)
        or not value["recovery_method"]
        or not isinstance(value.get("failed_at"), str)
        or not value["failed_at"]
    ):
        raise Refused("legacy failure artifact lacks sufficient recovery evidence")
    return value


def read_target_recreation_artifact(args: argparse.Namespace) -> dict[str, Any]:
    value = load_protected_artifact(
        cutover_state_path(args, TARGET_RECREATION_INITIATED_NAME)
    )
    if (
        not set(value).issubset({"schema", "container", "initiated_at", "live_mutation"})
        or
        value.get("schema") != 1
        or value.get("container") != CONTAINER
        or value.get("live_mutation") != "PERFORMED"
        or not isinstance(value.get("initiated_at"), str)
        or not value["initiated_at"]
    ):
        raise Refused("target recreation evidence is invalid")
    return value


def validate_no_target_container(
    runner: Runner,
    args: argparse.Namespace,
    current: dict[str, Any],
) -> None:
    """Require exactly the current source service and no target container."""
    target_ids = runner.run(
        [
            "docker",
            "ps",
            "-aq",
            "--no-trunc",
            "--filter",
            f"ancestor={TARGET_IMAGE}",
            "--format",
            "{{.ID}}",
        ]
    )
    if any(line.strip() for line in target_ids.splitlines()):
        raise Refused("v0.16.15 target container still exists")
    output = runner.run(
        [
            "docker",
            "ps",
            "-aq",
            "--no-trunc",
            "--filter",
            f"label=com.docker.compose.project={PROJECT}",
            "--filter",
            f"label=com.docker.compose.service={SERVICE}",
            "--format",
            "{{.ID}}",
        ]
    )
    identifiers = [line.strip() for line in output.splitlines() if line.strip()]
    if not identifiers:
        raise Refused("Stalwart Compose service container could not be resolved")
    seen_current = False
    for identifier in identifiers:
        if not re.fullmatch(r"[0-9a-f]{12,64}", identifier):
            raise Refused("Docker returned an invalid Stalwart container identity")
        values = runner.json(["docker", "inspect", identifier])
        if not isinstance(values, list) or len(values) != 1:
            raise Refused("Stalwart service container identity could not be inspected")
        snapshot = migration.snapshot_from_inspect(values[0])
        if snapshot["container_id"] == current["container_id"]:
            seen_current = True
            if snapshot["definition"]["image_ref"] != SOURCE_IMAGE:
                raise Refused("the current Stalwart container is not the approved source")
            continue
        if snapshot["definition"]["image_ref"] == TARGET_IMAGE:
            raise Refused("v0.16.15 target container still exists")
        raise Refused("an unexpected Stalwart service container still exists")
    if not seen_current:
        raise Refused("mas-stalwart-1 is not the sole Stalwart service container")


def validate_legacy_current_state(
    runner: Runner,
    args: argparse.Namespace,
    manifest: dict[str, Any],
    *,
    recovery_artifact: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, str]]:
    if cutover_success_path(args).exists() or cutover_phase_exists(
        args, VERIFICATION_COMPLETE_NAME
    ):
        raise Refused("legacy adoption is unavailable after a completed cutover")
    if cutover_phase_exists(args, TARGET_RUNNING_NAME):
        raise Refused("legacy adoption is unavailable after target verification began")
    runtime = prepare_runtime(runner, args)
    current = runtime["snapshot"]
    report = runtime["source_comparison"]
    if (
        not report["source_semantic_match"]
        or report["differing_fields"]
        or report["config_hash_drift_class"] not in {"NONE", "COMPOSE_METADATA"}
    ):
        raise Refused("source semantic validation is not adoption-safe")

    original = manifest["source"]
    if current.get("compose", {}).get("project") != original.get("compose", {}).get(
        "project"
    ) or current.get("compose", {}).get("service") != original.get("compose", {}).get(
        "service"
    ):
        raise RecoveryIdentityError("Compose project or service identity changed")

    original_id = str(original.get("container_id") or "")
    recovered_id = str(current.get("container_id") or "")
    if not re.fullmatch(r"[0-9a-f]{12,64}", original_id):
        raise RecoveryIdentityError("original source container identity is invalid")
    if not re.fullmatch(r"[0-9a-f]{12,64}", recovered_id):
        raise RecoveryIdentityError("recovered source container identity is invalid")
    if recovered_id != original_id:
        if recovery_artifact is None:
            try:
                read_legacy_failure(args)
                read_target_recreation_artifact(args)
            except Refused as exc:
                raise RecoveryIdentityError(
                    "recreated source lacks legacy recovery and target-recreation evidence"
                ) from exc
        elif (
            recovery_artifact.get("original_source_container_id") != original_id
            or recovery_artifact.get("recovered_source_container_id") != recovered_id
            or recovery_artifact.get("source_recovery_independently_verified") is not True
        ):
            raise RecoveryIdentityError("adoption artifact is not bound to the recovered source")

    differing_fields = source_definition_differences(original, current)
    hostname_regenerated = False
    if "hostname" in differing_fields:
        compose = parse_compose_json(runner, args, include_override=False)
        rendered_service = (compose.get("services") or {}).get(args.service)
        if not isinstance(rendered_service, dict) or not is_expected_docker_generated_hostname_change(
            original,
            current,
            rendered_service,
        ):
            raise RecoveryIdentityError("hostname change is not Docker-generated and recovery-safe")
        differing_fields.remove("hostname")
        hostname_regenerated = True
    if differing_fields:
        raise RecoveryIdentityError(
            "source definition differs from the approved manifest: "
            + ",".join(differing_fields)
        )

    if current.get("compose", {}).get("config_hash") != original.get("compose", {}).get(
        "config_hash"
    ):
        # A Compose recreation commonly changes this provenance label even when
        # the canonical runtime definition is unchanged. Make that allowance
        # explicit in the returned sanitized report rather than silently
        # treating the old hash as equivalent.
        if report["config_hash_drift_class"] not in {"NONE", "COMPOSE_METADATA"}:
            raise RecoveryIdentityError("config-hash drift is not metadata-only")
        report = {**report, "config_hash_drift_class": "COMPOSE_METADATA"}
    report = {
        **report,
        **source_hostname_metadata(
            original,
            current,
            hostname_regenerated=hostname_regenerated,
        ),
    }
    validate_no_target_container(runner, args, current)
    target_image_id, target_report = target_image_validation(runner)
    if target_image_id != manifest["target_image_id"]:
        raise Refused("local v0.16.15 target image identity changed")
    return current, report, target_report


def validate_adoption_artifact(
    runner: Runner,
    args: argparse.Namespace,
    manifest: dict[str, Any],
    artifact: dict[str, Any],
    current: dict[str, Any],
    report: dict[str, Any],
    target_report: dict[str, str],
) -> None:
    hostname_metadata = source_hostname_metadata(
        manifest["source"],
        current,
        hostname_regenerated=bool(report.get("source_hostname_regenerated")),
    )
    if (
        artifact.get("schema") != 1
        or artifact.get("kind") != "stalwart-legacy-failure-adoption"
        or not isinstance(artifact.get("adopted_at"), str)
        or not artifact["adopted_at"]
        or artifact.get("source_image") != SOURCE_IMAGE
        or artifact.get("adopted_legacy_artifact_sha256")
        != artifact.get("legacy_failure_artifact_sha256")
        or artifact.get("backup_manifest_fingerprint")
        != migration.canonical_hash(manifest)
        or artifact.get("original_source_container_id") != manifest["source"]["container_id"]
        or artifact.get("recovered_source_container_id") != current["container_id"]
        or artifact.get("source_container_id") != current["container_id"]
        or artifact.get("source_container_recreated")
        != (manifest["source"]["container_id"] != current["container_id"])
        or artifact.get("original_source_hostname")
        != hostname_metadata["original_source_hostname"]
        or artifact.get("recovered_source_hostname")
        != hostname_metadata["recovered_source_hostname"]
        or artifact.get("source_hostname_regenerated")
        != hostname_metadata["source_hostname_regenerated"]
        or artifact.get("hostname_source") != hostname_metadata["hostname_source"]
        or artifact.get("source_recovery_independently_verified") is not True
        or artifact.get("target_absence_verified") is not True
        or artifact.get("secret_source_match_verified") is not True
        or artifact.get("live_mutation") != "NOT_PERFORMED"
        or artifact.get("secret_value_or_fingerprint_stored") is not False
        or artifact.get("volumes_deleted_or_recreated") is not False
        or artifact.get("source_semantic_drift_class")
        != report["config_hash_drift_class"]
        or artifact.get("source_differing_fields") != []
        or artifact.get("target_image_validation") != target_report
    ):
        raise Refused("legacy adoption artifact conflicts with current verified state")
    archive = args.backup_dir / str(artifact.get("archive_relative_path") or "")
    if archive != legacy_adoption_archive_path(args):
        raise Refused("legacy adoption archive path is invalid")
    if not archive.is_dir():
        raise Refused("legacy adoption archive is missing")
    for name, field in (
        (CUTOVER_FAILURE_NAME, "legacy_failure_artifact_sha256"),
        (TARGET_RECREATION_INITIATED_NAME, "target_recreation_artifact_sha256"),
    ):
        original = cutover_state_path(args, name)
        archived = archive / name
        original_valid = original.is_file() and file_sha256(original) == artifact.get(field)
        archived_valid = archived.is_file() and file_sha256(archived) == artifact.get(field)
        if not archived_valid or (original.exists() and not original_valid):
            raise Refused("legacy adoption artifact or preserved archive was tampered")


def copy_legacy_artifact(source: Path, destination: Path) -> None:
    if source.is_symlink() or not source.is_file():
        raise Refused("legacy artifact is not a regular file")
    if hasattr(os, "geteuid"):
        details = source.stat()
        if details.st_uid != 0 or stat.S_IMODE(details.st_mode) != 0o600:
            raise Refused("legacy artifact is not root-owned mode 0600")
    if destination.exists():
        raise Refused("legacy adoption archive is already partially populated")
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp-{os.getpid()}")
    try:
        temporary.write_bytes(source.read_bytes())
        temporary.chmod(0o600)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def adopt_legacy_failure_action(runner: Runner, args: argparse.Namespace) -> None:
    require_root()
    require_exact_container_argument(args)
    manifest = read_manifest(args)
    read_backup_success(args, manifest)
    read_legacy_failure(args)
    read_target_recreation_artifact(args)
    current, report, target_report = validate_legacy_current_state(
        runner,
        args,
        manifest,
    )
    legacy_hash = file_sha256(cutover_state_path(args, CUTOVER_FAILURE_NAME))
    target_recreation_hash = file_sha256(
        cutover_state_path(args, TARGET_RECREATION_INITIATED_NAME)
    )
    artifact_path = legacy_adoption_path(args)
    if artifact_path.exists():
        artifact = load_protected_artifact(artifact_path)
        validate_adoption_artifact(
            runner,
            args,
            manifest,
            artifact,
            current,
            report,
            target_report,
        )
        print("ADOPTION_ALREADY_VERIFIED=PASS")
        print("LEGACY_FAILURE_ADOPTION=PASS")
        print("GOVERNED_RETRY_ELIGIBLE=PASS")
        print("SECRET_VALUE_OR_FINGERPRINT_OUTPUT=NONE")
        print("LIVE_MUTATION=NOT_PERFORMED")
        return
    archive = legacy_adoption_archive_path(args)
    if archive.exists():
        raise Refused("partial legacy adoption archive exists; refusing to overwrite it")
    archive.mkdir(mode=0o700, parents=True)
    archive.chmod(0o700)
    copy_legacy_artifact(
        cutover_state_path(args, CUTOVER_FAILURE_NAME),
        archive / CUTOVER_FAILURE_NAME,
    )
    copy_legacy_artifact(
        cutover_state_path(args, TARGET_RECREATION_INITIATED_NAME),
        archive / TARGET_RECREATION_INITIATED_NAME,
    )
    if file_sha256(archive / CUTOVER_FAILURE_NAME) != legacy_hash or file_sha256(
        archive / TARGET_RECREATION_INITIATED_NAME
    ) != target_recreation_hash:
        raise Refused("legacy adoption archive verification failed")
    adoption = {
        "schema": 1,
        "kind": "stalwart-legacy-failure-adoption",
        "adopted_legacy_artifact_sha256": legacy_hash,
        "legacy_failure_artifact_sha256": legacy_hash,
        "target_recreation_artifact_sha256": target_recreation_hash,
        "archive_relative_path": str(archive.relative_to(args.backup_dir)),
        "adopted_at": utc_now(),
        "original_source_container_id": manifest["source"]["container_id"],
        "recovered_source_container_id": current["container_id"],
        # Retain the compatibility alias for consumers of the previous
        # artifact schema; it is always bound to the recovered live ID.
        "source_container_id": current["container_id"],
        "source_container_recreated": (
            manifest["source"]["container_id"] != current["container_id"]
        ),
        **source_hostname_metadata(
            manifest["source"],
            current,
            hostname_regenerated=bool(report.get("source_hostname_regenerated")),
        ),
        "source_image": SOURCE_IMAGE,
        "backup_manifest_fingerprint": migration.canonical_hash(manifest),
        "source_recovery_independently_verified": True,
        "source_semantic_drift_class": report["config_hash_drift_class"],
        "source_differing_fields": [],
        "target_absence_verified": True,
        "target_image_validation": target_report,
        "secret_source_match_verified": True,
        "live_mutation": "NOT_PERFORMED",
        "secret_value_or_fingerprint_stored": False,
        "volumes_deleted_or_recreated": False,
    }
    migration.atomic_json(artifact_path, adoption)
    print("LEGACY_FAILURE_ADOPTION=PASS")
    print(f"ADOPTION_ARTIFACT={artifact_path}")
    print(f"ADOPTION_ARCHIVE={archive}")
    print("GOVERNED_RETRY_ELIGIBLE=PASS")
    print("SECRET_VALUE_OR_FINGERPRINT_OUTPUT=NONE")
    print("LIVE_MUTATION=NOT_PERFORMED")


def failure_diagnose_action(runner: Runner, args: argparse.Namespace) -> None:
    require_root()
    require_exact_container_argument(args)
    failure = read_cutover_failure(args)
    manifest = read_manifest(args)
    read_backup_success(args, manifest)
    needs_adoption = failure.get("error_code") == "legacy-failure-artifact"
    source_recovered = "FAIL"
    source_recreated = "NO"
    source_hostname_regenerated = "NO"
    original_source_id = str(manifest["source"].get("container_id") or "")
    recovered_source_id = "UNKNOWN"
    source_error_code = ""
    current_source: dict[str, Any] | None = None
    report: dict[str, Any] | None = None
    target_report: dict[str, str] | None = None
    try:
        if needs_adoption:
            current_source, report, target_report = validate_legacy_current_state(
                runner,
                args,
                manifest,
            )
        else:
            current_source = validate_recovered_source(runner, args)
            if (
                current_source["container_id"] != manifest["source"]["container_id"]
                or migration.normalized_definition(current_source["definition"])
                != migration.normalized_definition(manifest["source"]["definition"])
            ):
                raise Refused("recovered source does not match the approved manifest")
        source_recovered = "PASS"
        recovered_source_id = str(current_source.get("container_id") or "UNKNOWN")
        if recovered_source_id != original_source_id:
            source_recreated = "PASS"
        if report and report.get("source_hostname_regenerated") is True:
            source_hostname_regenerated = "PASS"
    except RecoveryIdentityError:
        source_error_code = RecoveryIdentityError.code
    except Refused:
        source_recovered = "FAIL"
    adoption_status = "NOT_REQUIRED"
    if needs_adoption:
        adoption_status = "NOT_PERFORMED"
        if legacy_adoption_path(args).exists():
            try:
                if current_source is None or report is None or target_report is None:
                    current_source, report, target_report = validate_legacy_current_state(
                        runner,
                        args,
                        manifest,
                    )
                adoption = load_protected_artifact(legacy_adoption_path(args))
                validate_adoption_artifact(
                    runner,
                    args,
                    manifest,
                    adoption,
                    current_source,
                    report,
                    target_report,
                )
                adoption_status = "PASS"
            except Refused:
                adoption_status = "FAIL"
    diagnosis_pass = source_recovered == "PASS" and (
        not needs_adoption or adoption_status == "PASS"
    )
    print("FAILURE_DIAGNOSIS=" + ("PASS" if diagnosis_pass else "FAIL"))
    print(f"FAILURE_STAGE={failure['failure_stage']}")
    print(f"EXCEPTION_TYPE={failure['exception_type']}")
    print(f"ERROR_CODE={source_error_code or failure['error_code']}")
    print(f"SANITIZED_MESSAGE={failure['sanitized_message']}")
    print(f"SOURCE_RECOVERED={source_recovered}")
    print(f"SOURCE_CONTAINER_RECREATED={source_recreated}")
    print(f"SOURCE_HOSTNAME_REGENERATED={source_hostname_regenerated}")
    print(f"ORIGINAL_SOURCE_CONTAINER_ID={original_source_id or 'UNKNOWN'}")
    print(f"RECOVERED_SOURCE_CONTAINER_ID={recovered_source_id}")
    print(f"LEGACY_FAILURE_ADOPTION={adoption_status}")
    print(
        "GOVERNED_RETRY_ELIGIBLE="
        + ("PASS" if diagnosis_pass else "NO")
    )
    print("SECRET_VALUE_OR_FINGERPRINT_OUTPUT=NONE")
    print("LIVE_MUTATION=NOT_PERFORMED")
    if not diagnosis_pass:
        raise Refused("failed cutover recovery is not currently adoption-safe")


def archive_attempt(args: argparse.Namespace) -> Path:
    history = attempt_history_path(args)
    history.mkdir(mode=0o700, parents=True, exist_ok=True)
    history.chmod(0o700)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    destination = history / f"attempt-{stamp}"
    suffix = 0
    while destination.exists():
        suffix += 1
        destination = history / f"attempt-{stamp}-{suffix}"
    destination.mkdir(mode=0o700)
    for name in ATTEMPT_ARTIFACT_NAMES:
        source = cutover_state_path(args, name)
        if not source.exists():
            continue
        if source.is_symlink() or not source.is_file():
            raise Refused("cutover attempt artifact is not a regular file")
        details = source.stat()
        if hasattr(os, "geteuid") and (
            details.st_uid != 0 or stat.S_IMODE(details.st_mode) != 0o600
        ):
            raise Refused("cutover attempt artifact is not protected")
        shutil.move(str(source), str(destination / name))
    return destination


def retry_action(runner: Runner, args: argparse.Namespace) -> None:
    require_root()
    require_exact_container_argument(args)
    if not args.approve_security_upgrade:
        raise Refused("--approve-security-upgrade is required for governed retry")
    manifest = read_manifest(args)
    read_backup_success(args, manifest)
    if cutover_success_path(args).exists() or cutover_phase_exists(
        args, VERIFICATION_COMPLETE_NAME
    ):
        raise Refused("security cutover is already complete; use verification only")
    failure = read_cutover_failure(args)
    if failure.get("source_auto_recovery") != "PASS":
        raise Refused("governed retry requires a passed source auto-recovery")
    legacy_adoption = None
    if failure.get("error_code") == "legacy-failure-artifact":
        if not legacy_adoption_path(args).exists():
            raise Refused("legacy failure requires adopt-legacy-failure first")
        legacy_adoption = load_protected_artifact(legacy_adoption_path(args))
    if failure.get("target_container_created") is True or cutover_phase_exists(
        args, TARGET_RUNNING_NAME
    ):
        raise Refused("prior attempt created a target; use verification-only recovery")
    if legacy_adoption is not None:
        current, report, target_report = validate_legacy_current_state(
            runner,
            args,
            manifest,
        )
        validate_adoption_artifact(
            runner,
            args,
            manifest,
            legacy_adoption,
            current,
            report,
            target_report,
        )
    else:
        current = prepare_runtime(runner, args)["snapshot"]
        if current != manifest["source"]:
            raise Refused("recovered v0.16.7 source does not match the approved manifest")
        target_image_id, _target_report = target_image_validation(runner)
        if target_image_id != manifest["target_image_id"]:
            raise Refused("local v0.16.15 target image identity changed")
        validate_no_target_container(runner, args, current)
    archive = archive_attempt(args)
    if legacy_adoption is not None:
        # The retry archives the legacy phase files before entering cutover.
        # Pass the already verified, immutable adoption artifact so the
        # cutover pre-stop phase can validate the recovered ID against the
        # archive rather than the now-moved root paths.
        args._legacy_recovery_artifact = legacy_adoption
    print(f"PREVIOUS_ATTEMPT_ARCHIVED={archive}")
    print("GOVERNED_RETRY=AUTHORIZED")
    cutover_action(runner, args)


def backup_action(runner: Runner, args: argparse.Namespace) -> None:
    require_root()
    require_exact_container_argument(args)
    manifest = read_manifest(args)
    success = backup_success_path(args)
    final_config = args.backup_dir / CONFIG_COPY
    final_data = args.backup_dir / DATA_COPY
    partial_config = args.backup_dir / f".{CONFIG_COPY}.partial"
    partial_data = args.backup_dir / f".{DATA_COPY}.partial"
    if success.exists():
        if not final_config.is_dir() or not final_data.is_dir():
            raise Refused("completed backup artifact exists but copied trees are missing")
        raise Refused("consistent backup is already complete; refusing to overwrite it")
    if any(
        path.exists()
        for path in (
            final_config,
            final_data,
            partial_config,
            partial_data,
            backup_failure_path(args),
        )
    ):
        raise Refused("partial backup state exists; refusing to overwrite or resume silently")

    current = prepare_runtime(runner, args)["snapshot"]
    if current != manifest["source"]:
        raise Refused("live Stalwart state changed after pre-upgrade inspection")

    partial_config.mkdir(mode=0o700)
    partial_data.mkdir(mode=0o700)
    stopped = False
    try:
        runner.run(["docker", "stop", args.container], timeout=120)
        stopped = True
        runner.run(
            ["docker", "cp", f"{args.container}:/etc/stalwart/.", str(partial_config)],
            timeout=600,
        )
        runner.run(
            ["docker", "cp", f"{args.container}:/var/lib/stalwart/.", str(partial_data)],
            timeout=1800,
        )
        config_stats = tree_stats(partial_config)
        data_stats = tree_stats(partial_data)
        os.replace(partial_config, final_config)
        os.replace(partial_data, final_data)
        runner.run(["docker", "start", args.container], timeout=120)
        stopped = False
        restored = wait_for_source_healthy(runner, args)
        if restored["container_id"] != manifest["source"]["container_id"]:
            raise Refused("backup restart did not preserve the original container")
        migration.atomic_json(
            success,
            {
                "schema": 1,
                "container": CONTAINER,
                "source_container_id": restored["container_id"],
                "manifest_fingerprint": migration.canonical_hash(manifest),
                "config_copy": CONFIG_COPY,
                "data_copy": DATA_COPY,
                "config_stats": config_stats,
                "data_stats": data_stats,
                "completed_at": utc_now(),
                "volumes_deleted_or_recreated": False,
            },
        )
    except Exception as exc:
        restart_error: Exception | None = None
        if stopped:
            try:
                runner.run(["docker", "start", args.container], timeout=120)
                wait_for_source_healthy(runner, args)
            except Exception as restart_exc:
                restart_error = restart_exc
        if not backup_failure_path(args).exists():
            migration.atomic_json(
                backup_failure_path(args),
                {
                    "schema": 1,
                    "container": CONTAINER,
                    "failed_at": utc_now(),
                    "original_container_restart": (
                        "FAIL" if restart_error is not None else "PASS"
                    ),
                    "secret_or_fingerprint_stored": False,
                },
            )
        if restart_error is not None:
            raise Refused(
                "backup failed and the original v0.16.7 container could not be restarted"
            ) from restart_error
        raise Refused(
            "backup failed safely; original v0.16.7 container was restarted"
        ) from exc

    print("STOPPED_CONSISTENT_BACKUP=PASS")
    print("ORIGINAL_V0.16.7_RESTARTED=PASS")
    print(f"CONFIG_COPY={final_config}")
    print(f"DATA_COPY={final_data}")
    print(f"BACKUP_SUCCESS={success}")
    print("DOCKER_VOLUME_MUTATION=NONE")
    print("SECRET_VALUE_OR_FINGERPRINT_OUTPUT=NONE")


def read_backup_success(args: argparse.Namespace, manifest: dict[str, Any]) -> dict[str, Any]:
    partial_paths = (
        args.backup_dir / f".{CONFIG_COPY}.partial",
        args.backup_dir / f".{DATA_COPY}.partial",
    )
    if backup_failure_path(args).exists() or any(path.exists() for path in partial_paths):
        raise Refused("partial or failed backup state exists")
    value = load_protected_artifact(backup_success_path(args))
    if (
        value.get("schema") != 1
        or value.get("container") != CONTAINER
        or value.get("manifest_fingerprint") != migration.canonical_hash(manifest)
        or value.get("volumes_deleted_or_recreated") is not False
    ):
        raise Refused("consistent backup success artifact is invalid or stale")
    for name, stats_key in (
        (CONFIG_COPY, "config_stats"),
        (DATA_COPY, "data_stats"),
    ):
        tree = args.backup_dir / name
        if not tree.is_dir():
            raise Refused(f"consistent backup tree is missing: {name}")
        if tree_stats(tree) != value.get(stats_key):
            raise Refused(f"consistent backup tree changed after completion: {name}")
    return value


def validate_upgraded(
    runner: Runner,
    args: argparse.Namespace,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    try:
        after = migration.wait_for_healthy(runner, args.container)
    except CommandError as exc:
        exc.stage = "TARGET_HEALTH_WAIT"
        raise
    except Refused as exc:
        raise LifecycleError(
            "target did not become healthy",
            code="health-timeout",
            stage="TARGET_HEALTH_WAIT",
        ) from exc
    args._target_post_validation = {"health": "PASS"}
    before = manifest["source"]
    definition = after["definition"]
    if definition["image_ref"] != TARGET_IMAGE:
        raise LifecycleError(
            "target image mismatch",
            code="target-image-mismatch",
            stage="TARGET_SEMANTIC_VALIDATION",
        )
    if definition["image_id"] != manifest["target_image_id"]:
        raise LifecycleError(
            "target image identity mismatch",
            code="target-image-mismatch",
            stage="TARGET_SEMANTIC_VALIDATION",
        )
    if after["container_id"] == before["container_id"]:
        raise LifecycleError(
            "target container ID was not newly created",
            code="target-container-id-mismatch",
            stage="TARGET_SEMANTIC_VALIDATION",
        )
    for field, message in (
        ("name", "container name changed"),
        ("mounts", "volume names, sources, or destinations changed"),
        ("ports", "published ports changed"),
        ("networks", "container networks changed"),
        ("restart_policy", "restart policy changed"),
        ("healthcheck", "healthcheck changed"),
    ):
        if definition[field] != before["definition"][field]:
            raise LifecycleError(
                message,
                code="target-semantic-mismatch",
                stage="TARGET_SEMANTIC_VALIDATION",
            )
    if not after["resend_secret_present"]:
        raise LifecycleError(
            "target secret environment is missing",
            code="target-secret-mismatch",
            stage="TARGET_SECRET_MATCH",
        )
    try:
        values = runner.json(["docker", "inspect", args.container])
    except CommandError as exc:
        exc.stage = "TARGET_SEMANTIC_VALIDATION"
        raise
    if not isinstance(values, list) or len(values) != 1:
        raise LifecycleError(
            "target container could not be inspected",
            code="target-inspect-failure",
            stage="TARGET_SEMANTIC_VALIDATION",
        )
    target_raw = values[0]
    try:
        target_report = compare_target_semantics(
            runner,
            args,
            target_raw,
            after,
        )
    except CommandError as exc:
        exc.stage = "TARGET_SEMANTIC_VALIDATION"
        raise
    except Refused as exc:
        raise LifecycleError(
            "target service could not be semantically validated",
            code="target-semantic-mismatch",
            stage="TARGET_SEMANTIC_VALIDATION",
        ) from exc
    args._target_post_validation.update(
        {
            "semantic": "PASS",
            "semantic_report": target_report,
        }
    )
    if not target_report["source_semantic_match"]:
        raise LifecycleError(
            "target service has material semantic drift",
            code="target-semantic-mismatch",
            stage="TARGET_SEMANTIC_VALIDATION",
        )
    try:
        migration.prepare_compose_environment(runner, args)
    except CommandError as exc:
        exc.stage = "TARGET_COMPOSE_IDENTITY"
        raise
    except Refused as exc:
        raise LifecycleError(
            "target Compose environment could not be prepared",
            code="compose-resolution-mismatch",
            stage="TARGET_COMPOSE_IDENTITY",
        ) from exc
    try:
        require_compose_container_identity(
            runner,
            args,
            after,
            include_override=True,
        )
    except Refused as exc:
        raise LifecycleError(
            "target Compose resolution mismatch",
            code="compose-resolution-mismatch",
            stage="TARGET_COMPOSE_IDENTITY",
        ) from exc
    args._target_post_validation["compose_identity"] = "PASS"
    secret = migration.protected_secret(args.secret_file)
    try:
        try:
            migration.secret_matches_container(runner, args.container, secret)
        except Refused as exc:
            raise LifecycleError(
                "target secret source mismatch",
                code="secret-verification-failure",
                stage="TARGET_SECRET_MATCH",
            ) from exc
    finally:
        secret = ""
    args._target_post_validation["secret"] = "PASS"
    return after


def write_cutover_success(
    args: argparse.Namespace,
    manifest: dict[str, Any],
    after: dict[str, Any],
    *,
    live_mutation: str = "PERFORMED",
) -> None:
    migration.atomic_json(
        cutover_success_path(args),
        {
            "schema": 1,
            "container": CONTAINER,
            "source_container_id": manifest["source"]["container_id"],
            "target_container_id": after["container_id"],
            "source_image": SOURCE_IMAGE,
            "target_image": TARGET_IMAGE,
            "manifest_fingerprint": migration.canonical_hash(manifest),
            "completed_at": utc_now(),
            "live_mutation": live_mutation,
            "secret_value_or_fingerprint_stored": False,
            "volumes_deleted_or_recreated": False,
        },
    )
    record_cutover_phase(
        args,
        VERIFICATION_COMPLETE_NAME,
        {
            "schema": 1,
            "container": CONTAINER,
            "completed_at": utc_now(),
            "live_mutation": live_mutation,
            "volumes_deleted_or_recreated": False,
        },
    )


def cutover_action(runner: Runner, args: argparse.Namespace) -> None:
    require_root()
    require_exact_container_argument(args)
    if not args.approve_security_upgrade:
        raise Refused("--approve-security-upgrade is required")
    manifest = read_manifest(args)
    read_backup_success(args, manifest)
    if cutover_success_path(args).exists() or cutover_phase_exists(
        args, VERIFICATION_COMPLETE_NAME
    ):
        raise Refused("security cutover is already complete; refusing a second cutover")
    if cutover_phase_exists(args, TARGET_RECREATION_INITIATED_NAME) or cutover_phase_exists(
        args, TARGET_RUNNING_NAME
    ):
        raise Refused(
            "target recreation already began; use verification-only recovery"
        )
    mutation = False
    target_recreation_started = False
    last_completed_phase = "PRE_STOP_VALIDATION"
    recovery: dict[str, Any] | None = None
    target_evidence = {
        "target_container_created": False,
        "target_became_healthy": False,
        "target_container_id": "",
    }
    try:
        if cutover_phase_exists(args, SOURCE_STOP_INITIATED_NAME):
            current_state = migration.inspect_container(runner, args.container)
            if not current_state["running"] or current_state["health"] != "healthy":
                mutation = True
                last_completed_phase = "SOURCE_STOP"
                recovery = recover_source(
                    runner,
                    args,
                    target_recreation_started=False,
                )
                print("SOURCE_AUTO_RECOVERY=" + recovery["source_auto_recovery"])

        # PRE_STOP_VALIDATION: every source and target precondition is checked
        # while the approved v0.16.7 container is still running and healthy.
        adopted_retry = getattr(args, "_legacy_recovery_artifact", None)
        if adopted_retry is not None:
            current, source_report, target_report = validate_legacy_current_state(
                runner,
                args,
                manifest,
                recovery_artifact=adopted_retry,
            )
            validate_adoption_artifact(
                runner,
                args,
                manifest,
                adopted_retry,
                current,
                source_report,
                target_report,
            )
        else:
            current = prepare_runtime(runner, args)["snapshot"]
            if current != manifest["source"]:
                raise Refused("live Stalwart state changed after the stopped backup")
            target_image_id, target_report = target_image_validation(runner)
            if target_image_id != manifest["target_image_id"]:
                raise Refused("local v0.16.15 target image identity changed")
        target_hash = migration.compose_service_hash(
            runner,
            args,
            include_override=True,
        )
        if target_hash != manifest["target_compose_hash"]:
            raise Refused("target Compose definition changed after inspection")
        record_cutover_phase(
            args,
            PRE_STOP_VALIDATION_NAME,
            {
                "schema": 1,
                "container": CONTAINER,
                "source_container_id": current["container_id"],
                "target_image_validation": target_report,
                "target_compose_hash": target_hash,
                "completed_at": utc_now(),
                "live_mutation": "NOT_PERFORMED",
            },
        )
        print("PRE_STOP_VALIDATION=PASS")

        # SOURCE_STOP: mutation is marked before invoking docker stop.
        record_cutover_phase(
            args,
            SOURCE_STOP_INITIATED_NAME,
            {
                "schema": 1,
                "container": CONTAINER,
                "stop_timeout_seconds": args.stop_timeout,
                "initiated_at": utc_now(),
                "live_mutation": "PERFORMED",
            },
        )
        mutation = True
        last_completed_phase = "SOURCE_STOP"
        runner.run(
            ["docker", "stop", "--time", str(args.stop_timeout), args.container],
            timeout=args.stop_timeout + 30,
        )
        stop_info = inspect_stopped_source(runner, args)
        record_cutover_phase(
            args,
            SOURCE_STOPPED_NAME,
            {
                "schema": 1,
                "container": CONTAINER,
                **stop_info,
                "completed_at": utc_now(),
                "live_mutation": "PERFORMED",
            },
        )
        last_completed_phase = "SOURCE_STOPPED"
        print("SOURCE_STOP=PASS")
        print("SOURCE_STOP_TIMEOUT_SECONDS=" + str(args.stop_timeout))
        print(
            "SOURCE_SIGKILL_REQUIRED="
            + ("PASS" if stop_info["sigkill_required"] else "NO")
        )
        if stop_info["sigkill_required"]:
            raise Refused("source stop required SIGKILL; cutover refused fail-closed")

        # TARGET_RECREATION: no source validator is called after the stop.
        record_cutover_phase(
            args,
            TARGET_RECREATION_INITIATED_NAME,
            {
                "schema": 1,
                "container": CONTAINER,
                "initiated_at": utc_now(),
                "live_mutation": "PERFORMED",
            },
        )
        target_recreation_started = True
        print("TARGET_RECREATION_INITIATED=PASS")
        runner.run(
            migration.compose_command(args, True)
            + [
                "up",
                "-d",
                "--no-deps",
                "--force-recreate",
                "--no-build",
                "--pull",
                "never",
                args.service,
            ],
            env=migration.compose_environment(args),
            timeout=300,
        )
        record_cutover_phase(
            args,
            TARGET_RECREATION_COMMAND_PASS_NAME,
            {
                "schema": 1,
                "container": CONTAINER,
                "completed_at": utc_now(),
                "live_mutation": "PERFORMED",
            },
        )
        last_completed_phase = "TARGET_RECREATION_COMMAND_PASS"
        print("TARGET_RECREATION_COMMAND_PASS=PASS")

        # TARGET_HEALTH_WAIT and POST_CUTOVER_VERIFICATION operate only on the
        # target definition; source running/health validation is not repeated.
        after = validate_upgraded(runner, args, manifest)
        post_validation = getattr(args, "_target_post_validation", None)
        if not isinstance(post_validation, dict) or not isinstance(
            post_validation.get("semantic_report"), dict
        ):
            raise LifecycleError(
                "target post-start validation did not produce a semantic report",
                code="target-semantic-mismatch",
                stage="POST_CUTOVER_VERIFICATION",
            )
        print("TARGET_HEALTH_PASS=PASS")
        print("TARGET_SEMANTIC_VALIDATION_PASS=PASS")
        print("TARGET_COMPOSE_IDENTITY_PASS=PASS")
        print("TARGET_SECRET_MATCH_PASS=PASS")
        record_cutover_phase(
            args,
            TARGET_RUNNING_NAME,
            {
                "schema": 1,
                "container": CONTAINER,
                "target_container_id": after["container_id"],
                "target_live_config_hash": post_validation["semantic_report"].get(
                    "live_config_hash", "NOT_RECORDED"
                ),
                "target_rendered_config_hash": post_validation[
                    "semantic_report"
                ].get("rendered_source_config_hash", "NOT_RECORDED"),
                "target_config_hash_drift_class": post_validation[
                    "semantic_report"
                ]["config_hash_drift_class"],
                "completed_at": utc_now(),
                "live_mutation": "PERFORMED",
            },
        )
        last_completed_phase = "TARGET_RUNNING"
        write_cutover_success(args, manifest, after)
        last_completed_phase = "POST_CUTOVER_VERIFICATION"
        print("STALWART_SECURITY_CUTOVER=PASS")
        print("SOURCE_VERSION=v0.16.7")
        print("TARGET_VERSION=v0.16.15")
        print("PERSISTENT_STORAGE_PRESERVED=PASS")
        print("RESEND_API_KEY_SOURCE_MATCH=PASS")
        print("LIVE_MUTATION=PERFORMED")
        print("LAST_COMPLETED_PHASE=POST_CUTOVER_VERIFICATION")
        print("DOCKER_VOLUME_MUTATION=NONE")
    except Exception as exc:
        target_evidence = target_failure_evidence(runner, args)
        if mutation:
            if not cutover_phase_exists(args, SOURCE_STOPPED_NAME):
                try:
                    stop_info = inspect_stopped_source(runner, args)
                    record_cutover_phase(
                        args,
                        SOURCE_STOPPED_NAME,
                        {
                            "schema": 1,
                            "container": CONTAINER,
                            **stop_info,
                            "completed_at": utc_now(),
                            "live_mutation": "PERFORMED",
                        },
                    )
                    last_completed_phase = "SOURCE_STOPPED"
                except Exception:
                    pass
            try:
                recovery = recover_source(
                    runner,
                    args,
                    target_recreation_started=target_recreation_started,
                )
            except Exception:
                recovery = {
                    "source_auto_recovery": "FAIL",
                    "recovery_method": "failed",
                }
            record_cutover_failure(
                args,
                last_completed_phase=last_completed_phase,
                mutation=True,
                recovery=recovery,
                exc=exc,
                target_recreation_started=target_recreation_started,
                target_evidence=target_evidence,
            )
            print("LIVE_MUTATION=PERFORMED")
            print(f"LAST_COMPLETED_PHASE={last_completed_phase}")
            print("SOURCE_AUTO_RECOVERY=" + recovery["source_auto_recovery"])
            if recovery["source_auto_recovery"] == "PASS":
                raise Refused(
                    "cutover failed safely after mutation; source auto-recovery passed"
                ) from exc
            raise Refused(
                "cutover failed after mutation and source auto-recovery failed"
            ) from exc
        record_cutover_failure(
            args,
            last_completed_phase=last_completed_phase,
            mutation=False,
            recovery=None,
            exc=exc,
            target_recreation_started=target_recreation_started,
            target_evidence=target_evidence,
        )
        print("LIVE_MUTATION=NOT_PERFORMED")
        print(f"LAST_COMPLETED_PHASE={last_completed_phase}")
        raise


def verify_action(runner: Runner, args: argparse.Namespace) -> None:
    require_root()
    require_exact_container_argument(args)
    manifest = read_manifest(args)
    read_backup_success(args, manifest)
    if cutover_success_path(args).exists():
        raise Refused("security cutover is already verified")
    after = validate_upgraded(runner, args, manifest)
    record_cutover_phase(
        args,
        TARGET_RUNNING_NAME,
        {
            "schema": 1,
            "container": CONTAINER,
            "target_container_id": after["container_id"],
            "completed_at": utc_now(),
            "live_mutation": "NOT_PERFORMED",
        },
    )
    write_cutover_success(args, manifest, after, live_mutation="NOT_PERFORMED")
    print("STALWART_SECURITY_CUTOVER_VERIFICATION=PASS")
    print("CONTAINER_RECREATION=NOT_PERFORMED")
    print("RESEND_API_KEY_SOURCE_MATCH=PASS")
    print("LIVE_MUTATION=NOT_PERFORMED")


def parser() -> argparse.ArgumentParser:
    script_dir = Path(__file__).resolve().parent
    gateway_dir = script_dir.parent
    infra_dir = gateway_dir.parent
    value = argparse.ArgumentParser()
    value.add_argument(
        "action",
        choices=(
            "diagnose",
            "failure-diagnose",
            "adopt-legacy-failure",
            "inspect",
            "backup-integrity",
            "backup",
            "cutover",
            "retry",
            "resume",
            "verify",
        ),
    )
    value.add_argument("--backup-dir", type=Path, required=True)
    value.add_argument("--container", default=CONTAINER)
    value.add_argument("--project-name", default=PROJECT)
    value.add_argument("--service", default=SERVICE)
    value.add_argument("--project-directory", type=Path, default=infra_dir / "compose")
    value.add_argument("--compose-profile", action="append", default=["mail-local"])
    value.add_argument("--compose-env-file", type=Path, action="append", default=[])
    value.add_argument(
        "--compose-file",
        type=Path,
        action="append",
        default=[
            gateway_dir / "home/docker-compose.stalwart-canonical.yml",
            gateway_dir / "home/docker-compose.stalwart-resend-secret.yml",
        ],
    )
    value.add_argument(
        "--override-file",
        type=Path,
        default=gateway_dir
        / "home/docker-compose.stalwart-v0.16.15-security-upgrade.yml",
    )
    value.add_argument(
        "--secret-file",
        type=Path,
        default=Path("/etc/aiat/stalwart-resend.env"),
    )
    value.add_argument("--persistent-target", default="/var/lib/stalwart")
    value.add_argument(
        "--stop-timeout",
        type=int,
        default=DEFAULT_STOP_TIMEOUT,
        help="graceful source stop timeout in seconds (default: 45)",
    )
    value.add_argument("--approve-security-upgrade", action="store_true")
    return value


def main(argv: list[str] | None = None, *, runner: Runner | None = None) -> int:
    args = parser().parse_args(argv)
    if not 30 <= args.stop_timeout <= 300:
        raise Refused("--stop-timeout must be between 30 and 300 seconds")
    args.backup_dir = args.backup_dir.resolve()
    args.project_directory = args.project_directory.resolve()
    args.compose_file = [path.resolve() for path in args.compose_file]
    args.compose_env_file = [path.resolve() for path in args.compose_env_file]
    args.override_file = args.override_file.resolve()
    args.secret_file = args.secret_file.resolve()
    selected_runner = runner or Runner()
    actions = {
        "diagnose": diagnose_action,
        "failure-diagnose": failure_diagnose_action,
        "adopt-legacy-failure": adopt_legacy_failure_action,
        "inspect": inspect_action,
        "backup-integrity": backup_integrity_action,
        "backup": backup_action,
        "cutover": cutover_action,
        "retry": retry_action,
        "resume": retry_action,
        "verify": verify_action,
    }
    actions[args.action](selected_runner, args)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Refused as exc:
        print(f"Stalwart security upgrade refused: {exc}", file=os.sys.stderr)
        raise SystemExit(1) from None
