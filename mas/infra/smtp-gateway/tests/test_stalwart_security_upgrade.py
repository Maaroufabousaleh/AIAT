from __future__ import annotations

import copy
import hashlib
import importlib.util
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[4]
SCRIPTS = ROOT / "mas" / "infra" / "smtp-gateway" / "scripts"

MIGRATION_SPEC = importlib.util.spec_from_file_location(
    "stalwart_secret_migration",
    SCRIPTS / "stalwart_secret_migration.py",
)
assert MIGRATION_SPEC and MIGRATION_SPEC.loader
migration = importlib.util.module_from_spec(MIGRATION_SPEC)
sys.modules["stalwart_secret_migration"] = migration
MIGRATION_SPEC.loader.exec_module(migration)

UPGRADE_SPEC = importlib.util.spec_from_file_location(
    "stalwart_security_upgrade",
    SCRIPTS / "stalwart_security_upgrade.py",
)
assert UPGRADE_SPEC and UPGRADE_SPEC.loader
upgrade = importlib.util.module_from_spec(UPGRADE_SPEC)
UPGRADE_SPEC.loader.exec_module(upgrade)


def inspect_fixture(*, secret: str = "re_protected_test_secret") -> dict:
    return {
        "Id": "a" * 64,
        "Name": "/mas-stalwart-1",
        "Image": "sha256:" + "b" * 64,
        "Config": {
            "Image": upgrade.SOURCE_IMAGE,
            "Hostname": "stalwart",
            "User": "",
            "Entrypoint": ["/usr/local/bin/stalwart-mail"],
            "Cmd": ["--config", "/etc/stalwart/config.toml"],
            "WorkingDir": "/opt/stalwart",
            "ExposedPorts": {"25/tcp": {}, "8080/tcp": {}},
            "Healthcheck": {
                "Test": [
                    "CMD",
                    "curl",
                    "-f",
                    "http://127.0.0.1:8080/healthz/ready",
                ]
            },
            "Env": [
                "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin",
                f"RESEND_API_KEY={secret}",
            ],
            "Labels": {
                "com.docker.compose.project": "mas",
                "com.docker.compose.service": "stalwart",
                "com.docker.compose.config-hash": "c" * 64,
                "aiat.role": "mail-authority",
            },
        },
        "HostConfig": {
            "PortBindings": {
                "25/tcp": [{"HostIp": "127.0.0.1", "HostPort": "2525"}],
                "8080/tcp": [{"HostIp": "127.0.0.1", "HostPort": "18080"}],
            },
            "RestartPolicy": {"Name": "unless-stopped", "MaximumRetryCount": 0},
            "NetworkMode": "mas_internal",
            "ReadonlyRootfs": False,
            "SecurityOpt": ["no-new-privileges:true"],
            "CapAdd": None,
            "CapDrop": None,
            "Privileged": False,
            "Memory": 805306368,
            "NanoCpus": 750000000,
            "LogConfig": {"Type": "json-file", "Config": {}},
        },
        "Mounts": [
            {
                "Type": "volume",
                "Name": "mas_stalwart_local_config",
                "Source": "/var/lib/docker/volumes/mas_stalwart_local_config/_data",
                "Destination": "/etc/stalwart",
                "RW": True,
                "Propagation": "",
            },
            {
                "Type": "volume",
                "Name": "mas_stalwart_local_data",
                "Source": "/var/lib/docker/volumes/mas_stalwart_local_data/_data",
                "Destination": "/var/lib/stalwart",
                "RW": True,
                "Propagation": "",
            },
        ],
        "NetworkSettings": {"Networks": {"mas_internal": {}, "mas_public": {}}},
        "State": {"Running": True, "Health": {"Status": "healthy"}},
    }


def source_snapshot(*, secret: str = "re_protected_test_secret") -> dict:
    return migration.snapshot_from_inspect(inspect_fixture(secret=secret))


def action_args(tmp_path: Path) -> SimpleNamespace:
    canonical = tmp_path / "canonical.yml"
    secret_override = tmp_path / "secret.yml"
    target_override = tmp_path / "target.yml"
    secret_file = tmp_path / "stalwart-resend.env"
    for path in (canonical, secret_override, target_override):
        path.write_text("services: {}\n", encoding="utf-8")
    secret_file.write_text("RESEND_API_KEY=re_protected_test_secret\n", encoding="utf-8")
    secret_file.chmod(0o600)
    return SimpleNamespace(
        backup_dir=tmp_path / "backup",
        container=upgrade.CONTAINER,
        project_name=upgrade.PROJECT,
        service=upgrade.SERVICE,
        project_directory=tmp_path,
        compose_profile=["mail-local"],
        compose_env_file=[],
        compose_file=[canonical, secret_override],
        override_file=target_override,
        secret_file=secret_file,
        persistent_target="/var/lib/stalwart",
        approve_security_upgrade=False,
    )


def manifest_for(snapshot: dict) -> dict:
    return {
        "schema": 1,
        "kind": "stalwart-v0.16.15-security-upgrade",
        "container": upgrade.CONTAINER,
        "project": upgrade.PROJECT,
        "service": upgrade.SERVICE,
        "source": snapshot,
        "source_image": upgrade.SOURCE_IMAGE,
        "target_image": upgrade.TARGET_IMAGE,
        "target_image_id": "sha256:" + "d" * 64,
        "target_compose_hash": "e" * 64,
        "compose_file_hashes": {},
        "sanitization": {
            "environment_values_stored": False,
            "secret_value_stored": False,
            "secret_fingerprint_stored": False,
            "label_values_stored": False,
        },
    }


def test_existing_secret_migration_inspect_still_refuses_present_secret() -> None:
    with pytest.raises(migration.Refused, match="RESEND_API_KEY must be absent"):
        migration.validate_snapshot(
            source_snapshot(),
            persistent_target="/var/lib/stalwart",
            require_secret=False,
        )


def test_security_upgrade_source_requires_present_secret() -> None:
    value = inspect_fixture(secret="")
    value["Config"]["Env"] = [
        item for item in value["Config"]["Env"] if not item.startswith("RESEND_API_KEY=")
    ]
    with pytest.raises(migration.Refused, match="RESEND_API_KEY must be present"):
        upgrade.validate_exact_source(migration.snapshot_from_inspect(value))


def test_secret_source_fingerprint_mismatch_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    args = action_args(tmp_path)
    value = source_snapshot()
    monkeypatch.setattr(migration, "inspect_container", lambda *_: value)
    monkeypatch.setattr(migration, "validate_mount_tracking", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(migration, "prepare_compose_environment", lambda *_: None)
    monkeypatch.setattr(migration, "require_compose_identity", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(migration, "protected_secret", lambda *_: "different-secret")

    def mismatch(*_args):
        raise migration.Refused("fingerprint mismatch")

    monkeypatch.setattr(migration, "secret_matches_container", mismatch)
    with pytest.raises(migration.Refused, match="fingerprint mismatch"):
        upgrade.prepare_runtime(SimpleNamespace(), args)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda value: value["definition"].update(image_ref="stalwart:v0.16.8"),
            "not pinned by digest",
        ),
        (
            lambda value: value["definition"]["mounts"][0].update(name="wrong"),
            "volume names or destinations drifted",
        ),
        (
            lambda value: value["definition"]["ports"]["25/tcp"][0].update(
                host_port="2526"
            ),
            "published ports drifted",
        ),
        (
            lambda value: value["definition"]["networks"].append("wrong"),
            "networks drifted",
        ),
    ],
)
def test_source_runtime_drift_is_rejected(mutation, message: str) -> None:
    value = source_snapshot()
    mutation(value)
    with pytest.raises(migration.Refused, match=message):
        upgrade.validate_exact_source(value)


@pytest.mark.parametrize(
    ("state_change", "message"),
    [
        (lambda raw: raw["State"].update(Running=False), "not running"),
        (
            lambda raw: raw["State"]["Health"].update(Status="unhealthy"),
            "not healthy",
        ),
    ],
)
def test_stopped_or_unhealthy_source_is_rejected(state_change, message: str) -> None:
    raw = inspect_fixture()
    state_change(raw)
    with pytest.raises(migration.Refused, match=message):
        upgrade.validate_exact_source(migration.snapshot_from_inspect(raw))


class ImageRunner:
    def __init__(self, repo_digests: list[str]):
        self.repo_digests = repo_digests

    def json(self, _args):
        return [{"Id": "sha256:" + "d" * 64, "RepoDigests": self.repo_digests}]


def test_wrong_target_digest_is_rejected() -> None:
    with pytest.raises(migration.Refused, match="does not match"):
        upgrade.validate_target_image_local(
            ImageRunner(["ghcr.io/stalwartlabs/stalwart@sha256:" + "f" * 64])
        )


def test_inspection_is_live_read_only_resume_safe_and_secret_free(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = action_args(tmp_path)
    args.backup_dir.mkdir(mode=0o700)
    value = source_snapshot()
    manifest = manifest_for(value)
    monkeypatch.setattr(upgrade, "require_root", lambda: None)
    monkeypatch.setattr(upgrade, "prepare_runtime", lambda *_: copy.deepcopy(value))
    monkeypatch.setattr(upgrade, "build_manifest", lambda *_: copy.deepcopy(manifest))
    monkeypatch.setattr(upgrade, "load_protected_artifact", migration.load_json)

    class NoMutationRunner:
        def run(self, command, **_kwargs):
            if any(word in command for word in ("stop", "start", "up", "cp")):
                raise AssertionError(f"mutating command during inspect: {command}")
            raise AssertionError(f"unexpected command during inspect: {command}")

    runner = NoMutationRunner()
    upgrade.inspect_action(runner, args)
    upgrade.inspect_action(runner, args)
    output = capsys.readouterr().out
    stored = upgrade.manifest_path(args).read_text(encoding="utf-8")
    secret = "re_protected_test_secret"
    secret_fingerprint = hashlib.sha256(secret.encode()).hexdigest()
    assert "LIVE_MUTATION=NOT_PERFORMED" in output
    assert "INSPECTION_RESUME=VERIFIED" in output
    assert secret not in output + stored
    assert secret_fingerprint not in output + stored
    if os.name != "nt":
        assert upgrade.manifest_path(args).stat().st_mode & 0o777 == 0o600


def test_backup_copy_failure_restarts_original_and_records_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    args = action_args(tmp_path)
    args.backup_dir.mkdir(mode=0o700)
    value = source_snapshot()
    manifest = manifest_for(value)
    monkeypatch.setattr(upgrade, "require_root", lambda: None)
    monkeypatch.setattr(upgrade, "read_manifest", lambda *_: manifest)
    monkeypatch.setattr(upgrade, "prepare_runtime", lambda *_: copy.deepcopy(value))
    monkeypatch.setattr(
        upgrade,
        "wait_for_source_healthy",
        lambda *_: copy.deepcopy(value),
    )

    class CopyFailureRunner:
        def __init__(self):
            self.commands: list[list[str]] = []

        def run(self, command, **_kwargs):
            self.commands.append(command)
            if command[:2] == ["docker", "cp"] and "/etc/stalwart/." in command[2]:
                destination = Path(command[3])
                (destination / "config.toml").write_text("test", encoding="utf-8")
            elif command[:2] == ["docker", "cp"]:
                raise migration.Refused("simulated copy failure")
            return ""

    runner = CopyFailureRunner()
    with pytest.raises(migration.Refused, match="original v0.16.7 container was restarted"):
        upgrade.backup_action(runner, args)
    assert ["docker", "stop", upgrade.CONTAINER] in runner.commands
    assert ["docker", "start", upgrade.CONTAINER] in runner.commands
    assert upgrade.backup_failure_path(args).is_file()
    assert not upgrade.backup_success_path(args).exists()


def test_partial_or_completed_backup_is_not_overwritten(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    args = action_args(tmp_path)
    args.backup_dir.mkdir(mode=0o700)
    (args.backup_dir / ".etc-stalwart.partial").mkdir()
    monkeypatch.setattr(upgrade, "require_root", lambda: None)
    monkeypatch.setattr(upgrade, "read_manifest", lambda *_: manifest_for(source_snapshot()))
    with pytest.raises(migration.Refused, match="partial backup state"):
        upgrade.backup_action(SimpleNamespace(), args)


def test_cutover_gate_rejects_partial_backup_even_with_success_artifact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    args = action_args(tmp_path)
    args.backup_dir.mkdir(mode=0o700)
    manifest = manifest_for(source_snapshot())
    (args.backup_dir / ".var-lib-stalwart.partial").mkdir()
    monkeypatch.setattr(
        upgrade,
        "load_protected_artifact",
        lambda *_: {
            "schema": 1,
            "container": upgrade.CONTAINER,
            "manifest_fingerprint": migration.canonical_hash(manifest),
            "volumes_deleted_or_recreated": False,
        },
    )
    with pytest.raises(migration.Refused, match="partial or failed backup state"):
        upgrade.read_backup_success(args, manifest)


def test_cutover_cannot_execute_twice(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    args = action_args(tmp_path)
    args.approve_security_upgrade = True
    args.backup_dir.mkdir(mode=0o700)
    upgrade.cutover_success_path(args).write_text("{}\n", encoding="utf-8")
    manifest = manifest_for(source_snapshot())
    monkeypatch.setattr(upgrade, "require_root", lambda: None)
    monkeypatch.setattr(upgrade, "read_manifest", lambda *_: manifest)
    monkeypatch.setattr(upgrade, "read_backup_success", lambda *_: {})

    class NoCommands:
        def run(self, command, **_kwargs):
            raise AssertionError(f"second cutover executed command: {command}")

    with pytest.raises(migration.Refused, match="already complete"):
        upgrade.cutover_action(NoCommands(), args)


def test_upgrade_source_contains_no_volume_delete_or_compose_down() -> None:
    source = (SCRIPTS / "stalwart_security_upgrade.py").read_text(encoding="utf-8")
    assert "docker volume rm" not in source
    assert "docker compose down" not in source
    assert "down -v" not in source
