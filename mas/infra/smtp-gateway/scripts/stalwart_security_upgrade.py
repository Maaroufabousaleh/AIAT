#!/usr/bin/env python3
"""Fail-closed v0.16.7 to v0.16.15 Stalwart security upgrade."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
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
TARGET_RUNNING_NAME = "target-running.json"
VERIFICATION_COMPLETE_NAME = "verification-complete.json"
CUTOVER_FAILURE_NAME = "cutover-failed.json"
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
Runner = migration.Runner


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


def parse_compose_json(runner: Runner, args: argparse.Namespace) -> dict[str, Any]:
    raw = runner.run(
        migration.compose_command(args, False)
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
) -> dict[str, Any]:
    service = (compose.get("services") or {}).get(args.service)
    if not isinstance(service, dict):
        raise Refused("canonical Compose source does not define stalwart")
    image = str(service.get("image") or "")
    if image != SOURCE_IMAGE:
        raise Refused("canonical Compose source image is not approved v0.16.7")
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
            str(path.resolve()) for path in args.compose_file
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
) -> None:
    if snapshot["compose"]["project"] != args.project_name:
        raise Refused("container Compose project does not match")
    if snapshot["compose"]["service"] != args.service:
        raise Refused("container Compose service does not match")
    current_id = runner.run(
        migration.compose_command(args, False) + ["ps", "-q", args.service],
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


def compare_source_semantics(
    runner: Runner,
    args: argparse.Namespace,
    raw: dict[str, Any],
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    compose = parse_compose_json(runner, args)
    live = live_semantics(raw)
    rendered = rendered_semantics(runner, args, compose)
    differing = sorted(
        field for field in rendered if live.get(field) != rendered.get(field)
    )
    service = (compose.get("services") or {}).get(args.service) or {}
    source_image_config = image_config(runner, SOURCE_IMAGE)
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
        include_override=False,
    )
    live_hash = snapshot["compose"]["config_hash"]
    hash_match = live_hash == rendered_hash
    provenance = repository_drift(
        runner,
        args,
        created_at=str(raw.get("Created") or ""),
    )
    if differing:
        drift_class = "MATERIAL_DRIFT"
    elif hash_match:
        drift_class = "NONE"
    elif (
        provenance["working_tree_changed"]
        or provenance["committed_after_container_creation"]
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


def record_cutover_failure(
    args: argparse.Namespace,
    *,
    last_completed_phase: str,
    mutation: bool,
    recovery: dict[str, Any] | None,
) -> None:
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
    after = migration.wait_for_healthy(runner, args.container)
    before = manifest["source"]
    definition = after["definition"]
    if definition["image_ref"] != TARGET_IMAGE:
        raise Refused("cutover did not start the approved v0.16.15 target digest")
    if after["container_id"] == before["container_id"]:
        raise Refused("cutover did not recreate the Stalwart container")
    for field, message in (
        ("name", "container name changed"),
        ("mounts", "volume names, sources, or destinations changed"),
        ("ports", "published ports changed"),
        ("networks", "container networks changed"),
        ("restart_policy", "restart policy changed"),
        ("healthcheck", "healthcheck changed"),
    ):
        if definition[field] != before["definition"][field]:
            raise Refused(message)
    if not after["resend_secret_present"]:
        raise Refused("RESEND_API_KEY was not preserved during cutover")
    migration.prepare_compose_environment(runner, args)
    migration.require_compose_identity(
        runner,
        args,
        after,
        include_override=True,
    )
    secret = migration.protected_secret(args.secret_file)
    try:
        migration.secret_matches_container(runner, args.container, secret)
    finally:
        secret = ""
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
        last_completed_phase = "TARGET_RECREATION"
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

        # TARGET_HEALTH_WAIT and POST_CUTOVER_VERIFICATION operate only on the
        # target definition; source running/health validation is not repeated.
        after = validate_upgraded(runner, args, manifest)
        record_cutover_phase(
            args,
            TARGET_RUNNING_NAME,
            {
                "schema": 1,
                "container": CONTAINER,
                "target_container_id": after["container_id"],
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
            "inspect",
            "backup-integrity",
            "backup",
            "cutover",
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
        "inspect": inspect_action,
        "backup-integrity": backup_integrity_action,
        "backup": backup_action,
        "cutover": cutover_action,
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
