#!/usr/bin/env python3
"""Fail-closed v0.16.7 to v0.16.15 Stalwart security upgrade."""

from __future__ import annotations

import argparse
import os
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
TARGET_PLATFORM_DIGEST = (
    "ghcr.io/stalwartlabs/stalwart@"
    "sha256:258b76c783f298500c5c065bebf09e1f9d773040803c5715b7c35357e529713c"
)
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
CONFIG_COPY = "etc-stalwart"
DATA_COPY = "var-lib-stalwart"

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


def validate_target_image_local(runner: Runner) -> str:
    values = runner.json(["docker", "image", "inspect", TARGET_IMAGE])
    if not isinstance(values, list) or len(values) != 1:
        raise Refused("approved v0.16.15 target digest is not locally present")
    raw = values[0]
    image_id = str(raw.get("Id") or "")
    repo_digests = set(raw.get("RepoDigests") or [])
    if not image_id.startswith("sha256:") or not (
        TARGET_IMAGE in repo_digests or TARGET_PLATFORM_DIGEST in repo_digests
    ):
        raise Refused("local v0.16.15 image does not match the approved target digest")
    return image_id


def prepare_runtime(runner: Runner, args: argparse.Namespace) -> dict[str, Any]:
    snapshot = migration.inspect_container(runner, args.container)
    validate_exact_source(snapshot)
    migration.validate_mount_tracking(runner, snapshot, project=args.project_name)
    migration.prepare_compose_environment(runner, args)
    migration.require_compose_identity(
        runner,
        args,
        snapshot,
        include_override=False,
    )
    secret = migration.protected_secret(args.secret_file)
    try:
        migration.secret_matches_container(runner, args.container, secret)
    finally:
        secret = ""
    return snapshot


def build_manifest(
    runner: Runner,
    args: argparse.Namespace,
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    target_image_id = validate_target_image_local(runner)
    target_compose_hash = migration.compose_service_hash(
        runner,
        args,
        include_override=True,
    )
    if target_compose_hash == snapshot["compose"]["config_hash"]:
        raise Refused("v0.16.15 override did not change the Compose service definition")
    return {
        "schema": 1,
        "kind": "stalwart-v0.16.15-security-upgrade",
        "container": CONTAINER,
        "project": PROJECT,
        "service": SERVICE,
        "source": snapshot,
        "source_image": SOURCE_IMAGE,
        "target_image": TARGET_IMAGE,
        "target_image_id": target_image_id,
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
    snapshot = prepare_runtime(runner, args)
    manifest = build_manifest(runner, args, snapshot)
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
    print("TARGET_IMAGE_LOCAL=APPROVED_V0.16.15")
    print("RESEND_API_KEY_PRESENT=PASS")
    print("RESEND_API_KEY_SOURCE_MATCH=PASS")
    print("SECRET_VALUE_OR_FINGERPRINT_OUTPUT=NONE")
    print("LIVE_MUTATION=NOT_PERFORMED")


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

    current = prepare_runtime(runner, args)
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
            "secret_value_or_fingerprint_stored": False,
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
    if cutover_success_path(args).exists():
        raise Refused("security cutover is already complete; refusing a second cutover")
    current = migration.inspect_container(runner, args.container)
    if current["definition"]["image_ref"] == TARGET_IMAGE:
        raise Refused(
            "v0.16.15 is already running without a success artifact; use verify only"
        )
    current = prepare_runtime(runner, args)
    if current != manifest["source"]:
        raise Refused("live Stalwart state changed after the stopped backup")
    if validate_target_image_local(runner) != manifest["target_image_id"]:
        raise Refused("local v0.16.15 target image identity changed")
    target_hash = migration.compose_service_hash(
        runner,
        args,
        include_override=True,
    )
    if target_hash != manifest["target_compose_hash"]:
        raise Refused("target Compose definition changed after inspection")
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
    after = validate_upgraded(runner, args, manifest)
    write_cutover_success(args, manifest, after)
    print("STALWART_SECURITY_CUTOVER=PASS")
    print("SOURCE_VERSION=v0.16.7")
    print("TARGET_VERSION=v0.16.15")
    print("PERSISTENT_STORAGE_PRESERVED=PASS")
    print("RESEND_API_KEY_SOURCE_MATCH=PASS")
    print("DOCKER_VOLUME_MUTATION=NONE")


def verify_action(runner: Runner, args: argparse.Namespace) -> None:
    require_root()
    require_exact_container_argument(args)
    manifest = read_manifest(args)
    read_backup_success(args, manifest)
    if cutover_success_path(args).exists():
        raise Refused("security cutover is already verified")
    after = validate_upgraded(runner, args, manifest)
    write_cutover_success(args, manifest, after)
    print("STALWART_SECURITY_CUTOVER_VERIFICATION=PASS")
    print("CONTAINER_RECREATION=NOT_PERFORMED")
    print("RESEND_API_KEY_SOURCE_MATCH=PASS")


def parser() -> argparse.ArgumentParser:
    script_dir = Path(__file__).resolve().parent
    gateway_dir = script_dir.parent
    infra_dir = gateway_dir.parent
    value = argparse.ArgumentParser()
    value.add_argument("action", choices=("inspect", "backup", "cutover", "verify"))
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
    value.add_argument("--approve-security-upgrade", action="store_true")
    return value


def main(argv: list[str] | None = None, *, runner: Runner | None = None) -> int:
    args = parser().parse_args(argv)
    args.backup_dir = args.backup_dir.resolve()
    args.project_directory = args.project_directory.resolve()
    args.compose_file = [path.resolve() for path in args.compose_file]
    args.compose_env_file = [path.resolve() for path in args.compose_env_file]
    args.override_file = args.override_file.resolve()
    args.secret_file = args.secret_file.resolve()
    selected_runner = runner or Runner()
    actions = {
        "inspect": inspect_action,
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
