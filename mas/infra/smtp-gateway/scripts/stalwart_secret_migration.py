#!/usr/bin/env python3
"""Fail-closed RESEND_API_KEY injection for an existing Compose Stalwart service."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import sys
import time
from typing import Any
from urllib import request


SECRET_NAME = "RESEND_API_KEY"
EXPECTED_DOMAIN = "agents.aiat.ca"
EXPECTED_ACCOUNT = "gateway-test@agents.aiat.ca"
EXPECTED_SMTP = ("127.0.0.1", 2525)
EXPECTED_JMAP = "http://127.0.0.1:18080"
EXPECTED_WIREGUARD_SMTP = ("10.77.0.2", 2525)
PINNED_IMAGE = re.compile(r"^.+@sha256:[0-9a-f]{64}$")
INTERNAL_COMPOSE_LABELS = {
    "com.docker.compose.config-hash",
    "com.docker.compose.project.config_files",
    "com.docker.compose.replace",
}
HASH_VALUE = re.compile(r"^[0-9a-f]{64}$")
SUCCESS_ARTIFACT = "post-migration-success.json"


class Refused(RuntimeError):
    pass


class Runner:
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
            raise Refused(f"command timed out safely: {args[0]}") from exc
        if completed.returncode != 0:
            # Command output is intentionally suppressed: Docker/Compose errors
            # can contain resolved environment values.
            raise Refused(f"command failed safely: {args[0]} {args[1] if len(args) > 1 else ''}".strip())
        return completed.stdout.strip()

    def diagnostic(
        self,
        args: list[str],
        *,
        env: dict[str, str],
        timeout: int = 60,
    ) -> tuple[int, str]:
        try:
            completed = subprocess.run(
                args,
                check=False,
                capture_output=True,
                text=True,
                env=env,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return 124, "Compose diagnostic timed out"
        return completed.returncode, completed.stderr

    def json(self, args: list[str]) -> Any:
        raw = self.run(args)
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise Refused(f"{args[0]} returned invalid JSON") from exc


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonical_hash(value: Any) -> str:
    return sha256_text(json.dumps(value, sort_keys=True, separators=(",", ":")))


def env_names(values: list[str]) -> list[str]:
    return sorted(item.split("=", 1)[0] for item in values if "=" in item)


def label_hashes(labels: dict[str, str]) -> dict[str, str]:
    return {
        key: sha256_text(str(value))
        for key, value in sorted(labels.items())
        if key not in INTERNAL_COMPOSE_LABELS
    }


def normalized_label_hashes(labels: dict[str, Any]) -> dict[str, str]:
    """Normalize both legacy raw-label maps and sanitized hash maps."""
    values: dict[str, str] = {}
    for key, raw_value in sorted(labels.items()):
        if key in INTERNAL_COMPOSE_LABELS:
            continue
        value = str(raw_value)
        values[key] = value.lower() if HASH_VALUE.fullmatch(value.lower()) else sha256_text(value)
    return values


def normalized_definition(definition: dict[str, Any]) -> dict[str, Any]:
    value = dict(definition)
    value["labels"] = normalized_label_hashes(value.get("labels") or {})
    return value


def normalized_mounts(raw: dict[str, Any]) -> list[dict[str, Any]]:
    values = []
    for mount in raw.get("Mounts") or []:
        values.append(
            {
                "type": mount.get("Type"),
                "name": mount.get("Name") or "",
                "source": mount.get("Source") or "",
                "destination": mount.get("Destination") or "",
                "rw": bool(mount.get("RW")),
                "propagation": mount.get("Propagation") or "",
            }
        )
    return sorted(values, key=lambda item: (item["destination"], item["source"]))


def normalized_ports(raw: dict[str, Any]) -> dict[str, Any]:
    bindings = ((raw.get("HostConfig") or {}).get("PortBindings") or {})
    return {
        key: sorted(
            [
                {
                    "host_ip": item.get("HostIp") or "",
                    "host_port": item.get("HostPort") or "",
                }
                for item in (value or [])
            ],
            key=lambda item: (item["host_ip"], item["host_port"]),
        )
        for key, value in sorted(bindings.items())
    }


def snapshot_from_inspect(raw: dict[str, Any]) -> dict[str, Any]:
    config = raw.get("Config") or {}
    host = raw.get("HostConfig") or {}
    state = raw.get("State") or {}
    labels = config.get("Labels") or {}
    image_ref = str(config.get("Image") or "")
    environment_names = env_names(config.get("Env") or [])
    definition = {
        "image_ref": image_ref,
        "image_id": raw.get("Image") or "",
        "name": str(raw.get("Name") or "").lstrip("/"),
        "hostname": config.get("Hostname") or "",
        "user": config.get("User") or "",
        "entrypoint_hash": canonical_hash(config.get("Entrypoint")),
        "cmd_hash": canonical_hash(config.get("Cmd")),
        "working_dir": config.get("WorkingDir") or "",
        "exposed_ports": config.get("ExposedPorts") or {},
        "healthcheck": canonical_hash(config.get("Healthcheck") or {}),
        "environment_names_without_resend": [
            name for name in environment_names if name != SECRET_NAME
        ],
        "labels": label_hashes(labels),
        "mounts": normalized_mounts(raw),
        "ports": normalized_ports(raw),
        "networks": sorted(((raw.get("NetworkSettings") or {}).get("Networks") or {}).keys()),
        "restart_policy": host.get("RestartPolicy") or {},
        "network_mode": host.get("NetworkMode") or "",
        "read_only_rootfs": bool(host.get("ReadonlyRootfs")),
        "security_opt": sorted(host.get("SecurityOpt") or []),
        "cap_add": sorted(host.get("CapAdd") or []),
        "cap_drop": sorted(host.get("CapDrop") or []),
        "privileged": bool(host.get("Privileged")),
        "memory": host.get("Memory") or 0,
        "nano_cpus": host.get("NanoCpus") or 0,
        "log_config_hash": canonical_hash(host.get("LogConfig") or {}),
    }
    return {
        "schema": 1,
        "container_id": raw.get("Id") or "",
        "definition": definition,
        "definition_fingerprint": canonical_hash(definition),
        "health": ((state.get("Health") or {}).get("Status") or "none"),
        "running": bool(state.get("Running")),
        "resend_secret_present": SECRET_NAME in environment_names,
        "compose": {
            "project": labels.get("com.docker.compose.project") or "",
            "service": labels.get("com.docker.compose.service") or "",
            "config_hash": labels.get("com.docker.compose.config-hash") or "",
        },
    }


def validate_snapshot(
    snapshot: dict[str, Any],
    *,
    persistent_target: str,
    require_secret: bool,
) -> None:
    definition = snapshot["definition"]
    if not PINNED_IMAGE.fullmatch(definition["image_ref"]):
        raise Refused("Stalwart image is not pinned by digest")
    mounts = definition["mounts"]
    persistent = [item for item in mounts if item["destination"] == persistent_target]
    if len(persistent) != 1:
        raise Refused(f"persistent Stalwart mount {persistent_target} cannot be identified")
    if not persistent[0]["source"]:
        raise Refused("persistent Stalwart mount has no source")
    if not snapshot["running"]:
        raise Refused("Stalwart container is not running")
    if snapshot["health"] != "healthy":
        raise Refused("Stalwart container is not healthy")
    if snapshot["resend_secret_present"] != require_secret:
        expected = "present" if require_secret else "absent"
        raise Refused(f"RESEND_API_KEY must be {expected}")


def validate_mount_tracking(
    runner: Runner,
    snapshot: dict[str, Any],
    *,
    project: str,
) -> None:
    for mount in snapshot["definition"]["mounts"]:
        if mount["type"] == "volume":
            if not mount["name"]:
                raise Refused(f"anonymous volume mounted at {mount['destination']} is not allowed")
            inspected = runner.json(["docker", "volume", "inspect", mount["name"]])
            if not isinstance(inspected, list) or len(inspected) != 1:
                raise Refused(f"volume {mount['name']} cannot be inspected")
            labels = inspected[0].get("Labels") or {}
            if labels.get("com.docker.compose.project") != project:
                raise Refused(f"volume {mount['name']} is not tracked by Compose project {project}")
        elif mount["type"] == "bind":
            source = Path(mount["source"])
            if not source.is_absolute() or not source.exists():
                raise Refused(f"bind mount {mount['destination']} is untracked or unavailable")
        else:
            raise Refused(f"unsupported or untracked mount type at {mount['destination']}")


def compare_preserved(
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    expect_secret: bool,
    require_health: bool = True,
) -> None:
    if after["resend_secret_present"] != expect_secret:
        raise Refused("RESEND_API_KEY presence does not match the requested migration state")
    before_definition = normalized_definition(before["definition"])
    after_definition = normalized_definition(after["definition"])
    for field, message in (
        ("image_ref", "image digest changed"),
        ("image_id", "image identity changed"),
        ("name", "container name changed"),
        ("mounts", "volume or mount source changed"),
        ("ports", "published ports changed"),
        ("networks", "container networks changed"),
        ("restart_policy", "restart policy changed"),
        ("labels", "configured labels changed"),
        ("healthcheck", "container healthcheck changed"),
    ):
        if before_definition[field] != after_definition[field]:
            raise Refused(message)
    if canonical_hash(before_definition) != canonical_hash(after_definition):
        raise Refused("container definition changed outside the approved secret injection")
    if require_health and (after["health"] != before["health"] or after["health"] != "healthy"):
        raise Refused("container health was not preserved")


def protected_secret(path: Path) -> str:
    try:
        details = path.stat()
    except FileNotFoundError as exc:
        raise Refused("protected RESEND_API_KEY file is missing") from exc
    if details.st_uid != 0:
        raise Refused("protected RESEND_API_KEY file must be root-owned")
    if stat.S_IMODE(details.st_mode) != 0o600:
        raise Refused("protected RESEND_API_KEY file must have mode 0600")
    lines = path.read_text(encoding="utf-8").splitlines()
    if len(lines) != 1 or not lines[0].startswith(f"{SECRET_NAME}="):
        raise Refused("protected injection file must contain exactly RESEND_API_KEY")
    secret = lines[0].split("=", 1)[1]
    if len(secret) < 20 or any(character.isspace() for character in secret):
        raise Refused("RESEND_API_KEY is missing or malformed")
    return secret


def certification_secrets(path: Path) -> dict[str, str]:
    try:
        details = path.stat()
    except FileNotFoundError as exc:
        raise Refused("certification credential file is missing") from exc
    if details.st_uid != 0 or stat.S_IMODE(details.st_mode) != 0o600:
        raise Refused("certification credential file must be root-owned mode 0600")
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    for key in ("STALWART_API_KEY", "STALWART_JMAP_SERVICE_TOKEN"):
        if not values.get(key):
            raise Refused(f"{key} is missing from the certification credential file")
    return values


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    if path.exists():
        raise Refused(f"refusing to overwrite migration artifact: {path}")
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
        temporary.chmod(0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise Refused(f"invalid or missing migration artifact: {path}") from exc
    if not isinstance(value, dict):
        raise Refused(f"invalid migration artifact: {path}")
    return value


def inspect_container(runner: Runner, container: str) -> dict[str, Any]:
    values = runner.json(["docker", "inspect", container])
    if not isinstance(values, list) or len(values) != 1:
        raise Refused("Docker did not return exactly one Stalwart container")
    return snapshot_from_inspect(values[0])


def compose_command(args: argparse.Namespace, include_override: bool) -> list[str]:
    command = ["docker", "compose"]
    for env_file in args.compose_env_file:
        command.extend(["--env-file", str(env_file)])
    command.extend([
        "--project-name",
        args.project_name,
        "--project-directory",
        str(args.project_directory),
    ])
    for profile in args.compose_profile:
        command.extend(["--profile", profile])
    for compose_file in args.compose_file:
        command.extend(["-f", str(compose_file)])
    if include_override:
        command.extend(["-f", str(args.override_file)])
    return command


def compose_environment(args: argparse.Namespace) -> dict[str, str]:
    values = dict(getattr(args, "render_environment", {}))
    values["STALWART_RESEND_SECRET_FILE"] = str(args.secret_file)
    return values


def prepare_compose_environment(runner: Runner, args: argparse.Namespace) -> None:
    values = runner.json(["docker", "inspect", args.container])
    if not isinstance(values, list) or len(values) != 1:
        raise Refused("Docker did not return exactly one Stalwart container")
    raw = values[0]
    container_env: dict[str, str] = {}
    for item in (raw.get("Config") or {}).get("Env") or []:
        if "=" in item:
            key, value = item.split("=", 1)
            if key in {"STALWART_PUBLIC_URL", "STALWART_RECOVERY_ADMIN"}:
                container_env[key] = value
    ports = ((raw.get("HostConfig") or {}).get("PortBindings") or {})
    try:
        container_env["STALWART_LOCAL_SMTP_PORT"] = ports["25/tcp"][0]["HostPort"]
        container_env["STALWART_LOCAL_ADMIN_PORT"] = ports["8080/tcp"][0]["HostPort"]
    except (KeyError, IndexError, TypeError) as exc:
        raise Refused("running Stalwart port bindings cannot be rendered safely") from exc
    safe_host_names = (
        "PATH",
        "HOME",
        "USER",
        "LOGNAME",
        "DOCKER_HOST",
        "DOCKER_CONTEXT",
        "XDG_RUNTIME_DIR",
        "WSL_DISTRO_NAME",
    )
    environment = {
        key: os.environ[key]
        for key in safe_host_names
        if key in os.environ
    }
    environment.update(container_env)
    args.render_environment = environment


def compose_service_hash(
    runner: Runner,
    args: argparse.Namespace,
    *,
    include_override: bool,
) -> str:
    output = runner.run(
        compose_command(args, include_override) + ["config", "--hash", args.service],
        env=compose_environment(args),
    )
    last_line = output.splitlines()[-1] if output else ""
    value = last_line.rsplit(maxsplit=1)[-1] if last_line else ""
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        raise Refused("Compose did not return a valid service configuration hash")
    return value


def require_compose_identity(
    runner: Runner,
    args: argparse.Namespace,
    snapshot: dict[str, Any],
    *,
    include_override: bool,
) -> str:
    if snapshot["compose"]["project"] != args.project_name:
        raise Refused("container Compose project does not match")
    if snapshot["compose"]["service"] != args.service:
        raise Refused("container Compose service does not match")
    current_id = runner.run(
        compose_command(args, include_override) + ["ps", "-q", args.service],
        env=compose_environment(args),
    )
    if current_id != snapshot["container_id"]:
        raise Refused("Compose does not resolve to the inspected Stalwart container")
    config_hash = compose_service_hash(runner, args, include_override=include_override)
    if snapshot["compose"]["config_hash"] != config_hash:
        raise Refused("running container does not match the selected Compose definition")
    return config_hash


def compose_file_hashes(args: argparse.Namespace) -> dict[str, str]:
    values: dict[str, str] = {}
    for path in [*args.compose_file, *args.compose_env_file, args.override_file]:
        if not path.is_file():
            raise Refused(f"Compose source is missing: {path}")
        values[str(path.resolve())] = hashlib.sha256(path.read_bytes()).hexdigest()
    return values


def require_file_hashes(args: argparse.Namespace, backup: dict[str, Any]) -> None:
    if compose_file_hashes(args) != backup.get("compose_file_hashes"):
        raise Refused("Compose source changed after the migration backup")


def backup_path(args: argparse.Namespace) -> Path:
    return args.backup_dir / "pre-migration-manifest.json"


def dry_run_path(args: argparse.Namespace) -> Path:
    return args.backup_dir / "dry-run.json"


def success_path(args: argparse.Namespace) -> Path:
    return args.backup_dir / SUCCESS_ARTIFACT


def read_backup(args: argparse.Namespace) -> dict[str, Any]:
    value = load_json(backup_path(args))
    if value.get("schema") != 1 or value.get("container") != args.container:
        raise Refused("migration backup does not match the requested container")
    return value


def inspect_action(runner: Runner, args: argparse.Namespace) -> None:
    if args.backup_dir.exists():
        raise Refused("backup directory already exists")
    snapshot = inspect_container(runner, args.container)
    prepare_compose_environment(runner, args)
    validate_snapshot(snapshot, persistent_target=args.persistent_target, require_secret=False)
    validate_mount_tracking(runner, snapshot, project=args.project_name)
    require_compose_identity(runner, args, snapshot, include_override=False)
    manifest = {
        "schema": 1,
        "container": args.container,
        "service": args.service,
        "project": args.project_name,
        "persistent_target": args.persistent_target,
        "compose_file_hashes": compose_file_hashes(args),
        "before": snapshot,
        "sanitization": {
            "environment_values_stored": False,
            "label_values_stored": False,
            "resend_secret_or_fingerprint_stored": False,
        },
    }
    atomic_json(backup_path(args), manifest)
    print("PRE_MIGRATION_MANIFEST=PASS")
    print(f"BACKUP_DIRECTORY={args.backup_dir}")
    print(f"IMAGE_DIGEST={snapshot['definition']['image_ref']}")
    print(f"PERSISTENT_MOUNT_SOURCE={next(item['source'] for item in snapshot['definition']['mounts'] if item['destination'] == args.persistent_target)}")
    print("RESEND_API_KEY_PRESENT=false")
    print("LIVE_MUTATION=NOT_PERFORMED")


def dry_run_action(runner: Runner, args: argparse.Namespace) -> None:
    backup = read_backup(args)
    require_file_hashes(args, backup)
    current = inspect_container(runner, args.container)
    prepare_compose_environment(runner, args)
    compare_preserved(backup["before"], current, expect_secret=False)
    validate_mount_tracking(runner, current, project=args.project_name)
    require_compose_identity(runner, args, current, include_override=False)
    secret = protected_secret(args.secret_file)
    proposed_hash = compose_service_hash(runner, args, include_override=True)
    if not proposed_hash or proposed_hash == current["compose"]["config_hash"]:
        raise Refused("secret-injection override did not produce a distinct Compose definition")
    atomic_json(
        dry_run_path(args),
        {
            "schema": 1,
            "container": args.container,
            "before_definition_fingerprint": current["definition_fingerprint"],
            "proposed_compose_hash": proposed_hash,
            "secret_source_validated": True,
            "direct_secret_value_or_fingerprint_stored": False,
            "compose_definition_hash_stored": True,
        },
    )
    secret = ""
    print("DRY_RUN=PASS")
    print("ONLY_CONTAINER_TO_RECREATE=stalwart")
    print("PRESERVE_IMAGE_MOUNTS_PORTS_NETWORKS_RESTART_LABELS=PASS")
    print("ADD_ENVIRONMENT_NAME=RESEND_API_KEY")
    print("SECRET_VALUE_OR_FINGERPRINT_OUTPUT=NONE")
    print("DOCKER_VOLUME_MUTATION=NONE")
    print("LIVE_MUTATION=NOT_PERFORMED")


def wait_for_healthy(runner: Runner, container: str, timeout: int = 120) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    latest: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        latest = inspect_container(runner, container)
        if latest["running"] and latest["health"] == "healthy":
            return latest
        time.sleep(2)
    raise Refused("recreated Stalwart container did not become healthy")


def secret_matches_container(runner: Runner, container: str, secret: str) -> None:
    command = (
        'test -n "$RESEND_API_KEY" || exit 11; '
        'command -v sha256sum >/dev/null 2>&1 || exit 12; '
        'printf %s "$RESEND_API_KEY" | sha256sum | awk "{print \\$1}"'
    )
    container_fingerprint = runner.run(["docker", "exec", container, "sh", "-c", command])
    if container_fingerprint != sha256_text(secret):
        raise Refused("running Stalwart RESEND_API_KEY does not match the protected source")


def require_dry_run(args: argparse.Namespace, backup: dict[str, Any]) -> dict[str, Any]:
    dry_run = load_json(dry_run_path(args))
    if (
        dry_run.get("schema") != 1
        or dry_run.get("container") != args.container
        or dry_run.get("before_definition_fingerprint") != backup["before"]["definition_fingerprint"]
        or not dry_run.get("secret_source_validated")
    ):
        raise Refused("approved dry-run artifact is missing or stale")
    return dry_run


def validate_apply_start(before: dict[str, Any], current: dict[str, Any]) -> None:
    if current["container_id"] != before["container_id"] or current["resend_secret_present"]:
        try:
            compare_preserved(before, current, expect_secret=True)
        except Refused as exc:
            raise Refused("live Stalwart state changed after backup; apply will not recreate it") from exc
        raise Refused(
            "Stalwart was already recreated with RESEND_API_KEY; use recover or verify"
        )
    compare_preserved(before, current, expect_secret=False)


def validate_recovery_state(before: dict[str, Any], current: dict[str, Any]) -> None:
    if current["container_id"] == before["container_id"]:
        raise Refused("Stalwart has not been recreated; recovery is not applicable")
    compare_preserved(before, current, expect_secret=True)


def apply_action(runner: Runner, args: argparse.Namespace) -> None:
    if not args.approve_recreate_stalwart:
        raise Refused("--approve-recreate-stalwart is required")
    backup = read_backup(args)
    dry_run = require_dry_run(args, backup)
    if success_path(args).exists():
        raise Refused("migration is already verified; apply will not recreate Stalwart")
    require_file_hashes(args, backup)
    current = inspect_container(runner, args.container)
    prepare_compose_environment(runner, args)
    validate_apply_start(backup["before"], current)
    require_compose_identity(runner, args, current, include_override=False)
    secret = protected_secret(args.secret_file)
    expected_hash = compose_service_hash(runner, args, include_override=True)
    if expected_hash != dry_run["proposed_compose_hash"]:
        raise Refused("proposed Compose definition changed after dry-run")
    runner.run(
        compose_command(args, True)
        + ["up", "-d", "--no-deps", "--force-recreate", "--no-build", "--pull", "never", args.service],
        env=compose_environment(args),
        timeout=180,
    )
    after = wait_for_healthy(runner, args.container)
    compare_preserved(backup["before"], after, expect_secret=True)
    require_compose_identity(runner, args, after, include_override=True)
    secret_matches_container(runner, args.container, secret)
    secret = ""
    print("SECRET_INJECTION_MIGRATION=PASS")
    print("ONLY_STALWART_CONTAINER_RECREATED=PASS")
    print("PERSISTENT_STORAGE_PRESERVED=PASS")
    print("RESEND_API_KEY_SOURCE_MATCH=PASS")
    print("DOCKER_VOLUME_MUTATION=NONE")


def post_json(url: str, authorization: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    message = request.Request(
        url,
        data=body,
        method="POST",
        headers={"Authorization": authorization, "Content-Type": "application/json"},
    )
    try:
        with request.urlopen(message, timeout=15) as response:
            value = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise Refused(f"local Stalwart endpoint verification failed at {url}") from exc
    if not isinstance(value, dict):
        raise Refused("local Stalwart returned an invalid response")
    return value


def first_method_response(value: dict[str, Any], name: str) -> dict[str, Any]:
    for item in value.get("methodResponses") or []:
        if isinstance(item, list) and len(item) >= 2 and item[0] == name and isinstance(item[1], dict):
            return item[1]
    raise Refused(f"Stalwart response did not include {name}")


def verify_stalwart_data(args: argparse.Namespace, credentials: dict[str, str]) -> None:
    admin_auth = f"Bearer {credentials['STALWART_API_KEY']}"
    domain_result = post_json(
        f"{EXPECTED_JMAP}/api",
        admin_auth,
        {
            "using": ["urn:ietf:params:jmap:core", "urn:stalwart:jmap"],
            "methodCalls": [
                ["x:Domain/query", {"filter": {"name": EXPECTED_DOMAIN}, "limit": 2}, "domain"],
            ],
        },
    )
    domain_ids = first_method_response(domain_result, "x:Domain/query").get("ids") or []
    if len(domain_ids) != 1:
        raise Refused(f"production domain {EXPECTED_DOMAIN} was not preserved")
    account_result = post_json(
        f"{EXPECTED_JMAP}/api",
        admin_auth,
        {
            "using": ["urn:ietf:params:jmap:core", "urn:stalwart:jmap"],
            "methodCalls": [
                [
                    "x:Account/query",
                    {"filter": {"name": "gateway-test", "domainId": str(domain_ids[0])}, "limit": 2},
                    "account",
                ],
            ],
        },
    )
    account_ids = first_method_response(account_result, "x:Account/query").get("ids") or []
    if account_ids != [args.account_id]:
        raise Refused(f"production account {EXPECTED_ACCOUNT} was not preserved")
    token = credentials["STALWART_JMAP_SERVICE_TOKEN"]
    authorization = token if token.startswith(("Bearer ", "Basic ", "OAuth ")) else f"Bearer {token}"
    mailbox_result = post_json(
        f"{EXPECTED_JMAP}/jmap",
        authorization,
        {
            "using": ["urn:ietf:params:jmap:core", "urn:ietf:params:jmap:mail"],
            "methodCalls": [["Mailbox/get", {"accountId": args.account_id}, "mailboxes"]],
        },
    )
    if not (first_method_response(mailbox_result, "Mailbox/get").get("list") or []):
        raise Refused("production mailbox data is unavailable through local JMAP")


def smtp_reachable(host: str, port: int) -> None:
    try:
        with socket.create_connection((host, port), timeout=10) as connection:
            connection.settimeout(10)
            greeting = connection.recv(2048)
            if not greeting.startswith(b"220"):
                raise Refused(f"SMTP listener {host}:{port} returned an invalid greeting")
            connection.sendall(b"EHLO migration-check.aiat.local\r\n")
            response = connection.recv(8192)
            if not response.startswith(b"250"):
                raise Refused(f"SMTP listener {host}:{port} rejected EHLO")
    except OSError as exc:
        raise Refused(f"required SMTP listener {host}:{port} is unreachable") from exc


def verify_completed_recreation(
    runner: Runner,
    args: argparse.Namespace,
    *,
    recovery: bool,
) -> None:
    backup = read_backup(args)
    dry_run = require_dry_run(args, backup)
    require_file_hashes(args, backup)
    after = wait_for_healthy(runner, args.container)
    prepare_compose_environment(runner, args)
    validate_recovery_state(backup["before"], after)
    validate_mount_tracking(runner, after, project=args.project_name)
    config_hash = require_compose_identity(runner, args, after, include_override=True)
    if config_hash != dry_run["proposed_compose_hash"]:
        raise Refused("running Stalwart does not match the approved dry-run definition")
    secret = protected_secret(args.secret_file)
    credentials = certification_secrets(args.verification_secret_file)
    secret_matches_container(runner, args.container, secret)
    verify_stalwart_data(args, credentials)
    smtp_reachable(*EXPECTED_SMTP)
    smtp_reachable(*EXPECTED_WIREGUARD_SMTP)
    secret = ""
    credentials.clear()
    artifact = {
        "schema": 1,
        "status": "PASS",
        "container": args.container,
        "original_container_id": backup["before"]["container_id"],
        "verified_container_id": after["container_id"],
        "verified_definition_fingerprint": canonical_hash(
            normalized_definition(after["definition"])
        ),
        "compose_config_hash": config_hash,
        "verified_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "secret_value_or_fingerprint_stored": False,
    }
    completed = success_path(args)
    if completed.exists():
        existing = load_json(completed)
        if existing != artifact and any(
            existing.get(key) != artifact.get(key)
            for key in (
                "schema",
                "status",
                "container",
                "original_container_id",
                "verified_container_id",
                "verified_definition_fingerprint",
                "compose_config_hash",
            )
        ):
            raise Refused("post-migration success artifact does not match the running container")
    else:
        atomic_json(completed, artifact)
    if recovery:
        print("POST_RECREATION_RECOVERY=PASS")
    print("POST_MIGRATION_VERIFICATION=PASS")
    print("IMAGE_DIGEST_PRESERVED=PASS")
    print("PERSISTENT_MOUNT_SOURCE_PRESERVED=PASS")
    print("PUBLISHED_PORTS_PRESERVED=PASS")
    print("NETWORKS_PRESERVED=PASS")
    print("CONTAINER_HEALTH_PRESERVED=PASS")
    print("RESEND_API_KEY_SOURCE_MATCH=PASS")
    print("PRODUCTION_DOMAIN_PRESERVED=PASS")
    print("GATEWAY_TEST_ACCOUNT_PRESERVED=PASS")
    print("LOCAL_SMTP_127_0_0_1_2525=PASS")
    print("LOCAL_JMAP_127_0_0_1_18080=PASS")
    print("WIREGUARD_SMTP_10_77_0_2_2525=PASS")
    print(f"SUCCESS_ARTIFACT={completed}")


def verify_action(runner: Runner, args: argparse.Namespace) -> None:
    verify_completed_recreation(runner, args, recovery=False)


def recover_action(runner: Runner, args: argparse.Namespace) -> None:
    verify_completed_recreation(runner, args, recovery=True)


def rollback_action(runner: Runner, args: argparse.Namespace) -> None:
    if not args.approve_rollback:
        raise Refused("--approve-rollback is required")
    if success_path(args).exists():
        raise Refused("rollback window closed after successful post-migration verification")
    backup = read_backup(args)
    require_file_hashes(args, backup)
    current = inspect_container(runner, args.container)
    prepare_compose_environment(runner, args)
    compare_preserved(backup["before"], current, expect_secret=True, require_health=False)
    # The original Compose definition is used without the secret override.
    # No volume command and no Compose down operation exists in this workflow.
    runner.run(
        compose_command(args, False)
        + ["up", "-d", "--no-deps", "--force-recreate", "--no-build", "--pull", "never", args.service],
        env=compose_environment(args),
        timeout=180,
    )
    restored = wait_for_healthy(runner, args.container)
    compare_preserved(backup["before"], restored, expect_secret=False)
    require_compose_identity(runner, args, restored, include_override=False)
    print("SECRET_INJECTION_ROLLBACK=PASS")
    print("RESEND_API_KEY_PRESENT=false")
    print("PERSISTENT_STORAGE_PRESERVED=PASS")
    print("DOCKER_VOLUME_MUTATION=NONE")


def sanitize_compose_stderr(stderr: str, args: argparse.Namespace) -> str:
    value = stderr
    protected_values = [
        item
        for key, item in getattr(args, "render_environment", {}).items()
        if key in {"STALWART_RECOVERY_ADMIN", "STALWART_PUBLIC_URL"} and item
    ]
    for protected in sorted(protected_values, key=len, reverse=True):
        value = value.replace(protected, "<redacted>")
    value = re.sub(
        r"(?i)\b([A-Z0-9_]*(?:PASSWORD|SECRET|TOKEN|API_KEY)[A-Z0-9_]*)=([^\s]+)",
        r"\1=<redacted>",
        value,
    )
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    return " | ".join(lines[:8])[:2000]


def compose_error_category(stderr: str) -> str:
    lowered = stderr.lower()
    if "depends on undefined service" in lowered:
        return "undefined_service_dependency"
    if "invalid compose project" in lowered:
        return "invalid_partial_compose_project"
    if "required variable" in lowered or "is missing a value" in lowered:
        return "unrelated_required_variable"
    return "compose_render_failure"


def diagnose_action(runner: Runner, args: argparse.Namespace) -> None:
    snapshot = inspect_container(runner, args.container)
    prepare_compose_environment(runner, args)
    command = compose_command(args, False) + ["config", "--hash", args.service]
    returncode, stderr = runner.diagnostic(command, env=compose_environment(args))
    if returncode != 0:
        print("COMPOSE_DIAGNOSTIC=FAIL")
        print(f"COMPOSE_ERROR_CATEGORY={compose_error_category(stderr)}")
        print(f"COMPOSE_STDERR_SANITIZED={sanitize_compose_stderr(stderr, args)}")
        print("LIVE_MUTATION=NOT_PERFORMED")
        raise Refused("Compose resolution failed; sanitized diagnostic emitted")
    require_compose_identity(runner, args, snapshot, include_override=False)
    print("COMPOSE_DIAGNOSTIC=PASS")
    print(f"RUNNING_CONTAINER_ID={snapshot['container_id']}")
    print(f"RUNNING_CONFIG_HASH={snapshot['compose']['config_hash']}")
    print("LIVE_MUTATION=NOT_PERFORMED")


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser()
    value.add_argument(
        "action",
        choices=("diagnose", "inspect", "dry-run", "apply", "recover", "verify", "rollback"),
    )
    value.add_argument("--container", default="mas-stalwart-1")
    value.add_argument("--service", default="stalwart")
    value.add_argument("--project-name", default="mas")
    value.add_argument("--project-directory", type=Path, required=True)
    value.add_argument("--compose-file", type=Path, action="append", required=True)
    value.add_argument("--compose-env-file", type=Path, action="append", default=[])
    value.add_argument("--compose-profile", action="append", default=[])
    value.add_argument(
        "--override-file",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "home" / "docker-compose.stalwart-resend-secret.yml",
    )
    value.add_argument("--secret-file", type=Path, required=True)
    value.add_argument("--verification-secret-file", type=Path)
    value.add_argument("--backup-dir", type=Path, required=True)
    value.add_argument("--persistent-target", default="/var/lib/stalwart")
    value.add_argument("--account-id")
    value.add_argument("--approve-recreate-stalwart", action="store_true")
    value.add_argument("--approve-rollback", action="store_true")
    return value


def main(argv: list[str] | None = None, *, runner: Runner | None = None) -> int:
    os.umask(0o077)
    args = parser().parse_args(argv)
    args.project_directory = args.project_directory.resolve()
    args.compose_file = [path.resolve() for path in args.compose_file]
    args.compose_env_file = [path.resolve() for path in args.compose_env_file]
    if not args.compose_profile:
        args.compose_profile = ["mail-local"]
    args.override_file = args.override_file.resolve()
    args.secret_file = args.secret_file.resolve()
    args.backup_dir = args.backup_dir.resolve()
    if args.verification_secret_file:
        args.verification_secret_file = args.verification_secret_file.resolve()
    if args.action in {"recover", "verify"} and (
        not args.verification_secret_file or not args.account_id
    ):
        raise Refused(f"{args.action} requires --verification-secret-file and --account-id")
    active_runner = runner or Runner()
    actions = {
        "diagnose": diagnose_action,
        "inspect": inspect_action,
        "dry-run": dry_run_action,
        "apply": apply_action,
        "recover": recover_action,
        "verify": verify_action,
        "rollback": rollback_action,
    }
    actions[args.action](active_runner, args)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Refused as exc:
        print(f"Stalwart secret migration refused: {exc}", file=sys.stderr)
        raise SystemExit(1)
