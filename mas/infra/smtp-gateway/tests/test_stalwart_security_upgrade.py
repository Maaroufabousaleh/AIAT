from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
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


ORIGINAL_RECOVERY_SOURCE_ID = (
    "c39df8e08f6d38367fdddbafe6a5e9f126576f375ef7a58ea9921273618c6dff"
)
RECOVERED_SOURCE_ID = (
    "50575adb3fb60538612bba80611301a118f23c1e77c3e2bf49cdab50fd205938"
)


def recovered_source_pair() -> tuple[dict, dict]:
    original = source_snapshot()
    original["container_id"] = ORIGINAL_RECOVERY_SOURCE_ID
    recovered = copy.deepcopy(original)
    recovered["container_id"] = RECOVERED_SOURCE_ID
    return original, recovered


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
        stop_timeout=45,
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
        "target_image_validation": {
            "target_repository_match": "PASS",
            "target_digest_match": "PASS",
            "target_platform": "linux/amd64",
        },
        "target_compose_hash": "e" * 64,
        "compose_file_hashes": {},
        "sanitization": {
            "environment_values_stored": False,
            "secret_value_stored": False,
            "secret_fingerprint_stored": False,
            "label_values_stored": False,
        },
    }


def matching_compose() -> dict:
    return {
        "name": "mas",
        "services": {
            "stalwart": {
                "image": upgrade.SOURCE_IMAGE,
                "command": None,
                "entrypoint": None,
                "environment": {
                    "STALWART_PUBLIC_URL": "http://localhost:18080",
                    "STALWART_RECOVERY_ADMIN": "",
                },
                "healthcheck": {
                    "test": [
                        "CMD",
                        "curl",
                        "-f",
                        "http://127.0.0.1:8080/healthz/ready",
                    ],
                    "interval": "0s",
                    "timeout": "0s",
                    "start_period": "0s",
                    "retries": 0,
                },
                "mem_limit": "805306368",
                "cpus": 0.75,
                "networks": {"internal": None, "public": None},
                "ports": [
                    {
                        "host_ip": "127.0.0.1",
                        "target": 25,
                        "published": "2525",
                        "protocol": "tcp",
                    },
                    {
                        "host_ip": "127.0.0.1",
                        "target": 8080,
                        "published": "18080",
                        "protocol": "tcp",
                    },
                ],
                "restart": "unless-stopped",
                "security_opt": ["no-new-privileges:true"],
                "volumes": [
                    {
                        "type": "volume",
                        "source": "stalwart_local_config",
                        "target": "/etc/stalwart",
                        "volume": {},
                    },
                    {
                        "type": "volume",
                        "source": "stalwart_local_data",
                        "target": "/var/lib/stalwart",
                        "volume": {},
                    },
                ],
            }
        },
        "networks": {
            "internal": {"name": "mas_internal"},
            "public": {"name": "mas_public"},
        },
        "volumes": {
            "stalwart_local_config": {"name": "mas_stalwart_local_config"},
            "stalwart_local_data": {"name": "mas_stalwart_local_data"},
        },
    }


class SemanticRunner:
    def __init__(
        self,
        raw: dict,
        compose: dict,
        *,
        rendered_hash: str = "f" * 64,
        repository_changed: bool = False,
    ):
        self.raw = raw
        self.compose = compose
        self.rendered_hash = rendered_hash
        self.repository_changed = repository_changed
        self.commands: list[list[str]] = []

    def run(self, command, **_kwargs):
        self.commands.append(command)
        if command[:2] == ["docker", "compose"] and "--format" in command:
            return json.dumps(self.compose)
        if command[:2] == ["docker", "compose"] and "--hash" in command:
            return self.rendered_hash
        if command[:2] == ["git", "-C"] and "rev-parse" in command:
            return str(ROOT)
        if command[:2] == ["git", "-C"] and "status" in command:
            return " M canonical.yml" if self.repository_changed else ""
        if command[:2] == ["git", "-C"] and "log" in command:
            return "2020-01-01T00:00:00+00:00"
        raise AssertionError(f"unexpected command: {command}")

    def json(self, command):
        self.commands.append(command)
        if command[:3] == ["docker", "image", "inspect"]:
            config = copy.deepcopy(self.raw["Config"])
            config.pop("Labels", None)
            config.pop("Env", None)
            config["Env"] = ["PATH=/usr/local/bin"]
            return [{"Config": config}]
        raise AssertionError(f"unexpected JSON command: {command}")


def semantic_case(
    tmp_path: Path,
    *,
    live_hash: str = "3" * 64,
) -> tuple[SimpleNamespace, dict, dict]:
    args = action_args(tmp_path)
    raw = inspect_fixture()
    raw["Created"] = "2026-07-30T00:00:00Z"
    raw["Config"]["Labels"].pop("aiat.role")
    raw["Config"]["Labels"]["com.docker.compose.config-hash"] = live_hash
    raw["Config"]["Env"] = [
        "PATH=/usr/local/bin",
        "STALWART_PUBLIC_URL=http://localhost:18080",
        "STALWART_RECOVERY_ADMIN=",
        "RESEND_API_KEY=re_protected_test_secret",
    ]
    compose = matching_compose()
    compose["services"]["stalwart"]["env_file"] = [
        {"path": str(args.secret_file), "format": "raw"}
    ]
    raw["Config"]["Labels"].update(
        {
            "com.docker.compose.project.working_dir": str(
                args.project_directory.resolve()
            ),
            "com.docker.compose.project.config_files": ",".join(
                str(path.resolve()) for path in args.compose_file
            ),
        }
    )
    return args, raw, compose


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
    monkeypatch.setattr(migration, "validate_mount_tracking", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(migration, "prepare_compose_environment", lambda *_: None)
    monkeypatch.setattr(
        upgrade,
        "compare_source_semantics",
        lambda *_: {
            "source_semantic_match": True,
            "config_hash_match": False,
            "config_hash_drift_class": "COMPOSE_METADATA",
            "differing_fields": [],
        },
    )
    monkeypatch.setattr(
        upgrade,
        "require_compose_container_identity",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(migration, "protected_secret", lambda *_: "different-secret")

    def mismatch(*_args):
        raise migration.Refused("fingerprint mismatch")

    monkeypatch.setattr(migration, "secret_matches_container", mismatch)
    runner = SimpleNamespace(json=lambda _command: [inspect_fixture()])
    with pytest.raises(migration.Refused, match="fingerprint mismatch"):
        upgrade.prepare_runtime(runner, args)


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
    def __init__(
        self,
        repo_digests: list[str],
        *,
        repo_tags: list[str] | None = None,
        image_id: str | None = None,
        os_name: str = "linux",
        architecture: str = "amd64",
    ):
        self.repo_digests = repo_digests
        self.repo_tags = repo_tags or []
        self.image_id = image_id or "sha256:" + "d" * 64
        self.os_name = os_name
        self.architecture = architecture

    def json(self, _args):
        return [
            {
                "Id": self.image_id,
                "RepoDigests": self.repo_digests,
                "RepoTags": self.repo_tags,
                "Os": self.os_name,
                "Architecture": self.architecture,
            }
        ]


def test_wrong_target_digest_is_rejected() -> None:
    with pytest.raises(migration.Refused, match="does not match"):
        upgrade.validate_target_image_local(
            ImageRunner(["ghcr.io/stalwartlabs/stalwart@sha256:" + "f" * 64])
        )


def test_exact_live_docker_desktop_target_identity_passes() -> None:
    expected = upgrade.TARGET_REPOSITORY_DIGEST
    image_id, report = upgrade.target_image_validation(
        ImageRunner(
            [expected],
            repo_tags=[expected],
            image_id="sha256:4f926193e5dd9ceb1e24ba48160702310381b12e51972c2fb0cc9de020388136",
        )
    )
    assert image_id.endswith("4f926193e5dd9ceb1e24ba48160702310381b12e51972c2fb0cc9de020388136")
    assert report == {
        "target_repository_match": "PASS",
        "target_digest_match": "PASS",
        "target_platform": "linux/amd64",
    }


def test_tag_at_digest_is_normalized_to_repository_at_digest() -> None:
    assert upgrade.normalize_repository_digest(upgrade.TARGET_IMAGE) == (
        upgrade.TARGET_REPOSITORY_DIGEST
    )
    with pytest.raises(ValueError):
        upgrade.normalize_repository_digest(
            "ghcr.io/stalwartlabs/stalwart:v0.16.15"
        )


@pytest.mark.parametrize(
    "metadata",
    [
        {"repo_digests": ["ghcr.io/other/stalwart@sha256:" + "4" * 64]},
        {"repo_digests": ["ghcr.io/stalwartlabs/stalwart@sha256:" + "f" * 64]},
        {"repo_digests": [], "repo_tags": ["ghcr.io/stalwartlabs/stalwart:v0.16.15"]},
        {
            "repo_digests": [
                "ghcr.io/stalwartlabs/stalwart@sha256:258b76c783f298500c5c065bebf09e1f9d773040803c5715b7c35357e529713c"
            ]
        },
    ],
)
def test_wrong_repository_digest_tag_only_and_stale_platform_identity_fail(metadata) -> None:
    with pytest.raises(migration.Refused, match="repository@digest"):
        upgrade.validate_target_image_local(ImageRunner(**metadata))


def test_missing_repo_digests_accepts_exact_docker_desktop_digest_tag() -> None:
    upgrade.validate_target_image_local(
        ImageRunner([], repo_tags=[upgrade.TARGET_REPOSITORY_DIGEST])
    )


@pytest.mark.parametrize(
    ("os_name", "architecture"),
    [("windows", "amd64"), ("linux", "arm64"), ("", "")],
)
def test_wrong_target_platform_fails_closed(os_name: str, architecture: str) -> None:
    with pytest.raises(migration.Refused, match="linux/amd64"):
        upgrade.validate_target_image_local(
            ImageRunner(
                [upgrade.TARGET_REPOSITORY_DIGEST],
                os_name=os_name,
                architecture=architecture,
            )
        )


def test_same_semantics_with_different_compose_hash_is_allowed(
    tmp_path: Path,
) -> None:
    args, raw, compose = semantic_case(tmp_path)
    snapshot = migration.snapshot_from_inspect(raw)
    report = upgrade.compare_source_semantics(
        SemanticRunner(raw, compose, rendered_hash="f" * 64),
        args,
        raw,
        snapshot,
    )
    assert report["source_semantic_match"] is True
    assert report["config_hash_match"] is False
    assert report["config_hash_drift_class"] == "COMPOSE_METADATA"
    assert report["differing_fields"] == []


def test_healthy_target_with_metadata_only_hash_drift_is_allowed(
    tmp_path: Path,
) -> None:
    args, raw, compose = semantic_case(tmp_path)
    compose["services"]["stalwart"]["image"] = upgrade.TARGET_IMAGE
    raw["Config"]["Image"] = upgrade.TARGET_IMAGE
    raw["Config"]["Labels"]["com.docker.compose.project.config_files"] = ",".join(
        [*(str(path.resolve()) for path in args.compose_file), str(args.override_file.resolve())]
    )
    snapshot = migration.snapshot_from_inspect(raw)
    report = upgrade.compare_target_semantics(
        SemanticRunner(raw, compose, rendered_hash="e" * 64),
        args,
        raw,
        snapshot,
    )
    assert report["source_semantic_match"] is True
    assert report["config_hash_match"] is False
    assert report["config_hash_drift_class"] == "COMPOSE_METADATA"
    assert report["differing_fields"] == []


@pytest.mark.parametrize(
    ("mutation", "field"),
    [
        (
            lambda _raw, compose: compose["services"]["stalwart"].update(
                command=["--different", "/etc/stalwart/config.toml"]
            ),
            "command",
        ),
        (
            lambda raw, _compose: raw["HostConfig"]["PortBindings"]["25/tcp"][0].update(
                HostPort="2526"
            ),
            "ports",
        ),
        (
            lambda raw, _compose: raw["NetworkSettings"]["Networks"].update(
                unexpected={}
            ),
            "networks",
        ),
    ],
)
def test_target_material_drift_is_rejected(
    tmp_path: Path,
    mutation,
    field: str,
) -> None:
    args, raw, compose = semantic_case(tmp_path)
    compose["services"]["stalwart"]["image"] = upgrade.TARGET_IMAGE
    raw["Config"]["Image"] = upgrade.TARGET_IMAGE
    raw["Config"]["Labels"]["com.docker.compose.project.config_files"] = ",".join(
        [*(str(path.resolve()) for path in args.compose_file), str(args.override_file.resolve())]
    )
    mutation(raw, compose)
    report = upgrade.compare_target_semantics(
        SemanticRunner(raw, compose),
        args,
        raw,
        migration.snapshot_from_inspect(raw),
    )
    assert report["source_semantic_match"] is False
    assert report["config_hash_drift_class"] == "MATERIAL_DRIFT"
    assert field in report["differing_fields"]


def test_validate_upgraded_requires_new_exact_target_image_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    args = action_args(tmp_path)
    source = source_snapshot()
    manifest = manifest_for(source)
    target_raw = inspect_fixture()
    target_raw["Id"] = "e" * 64
    target_raw["Config"]["Image"] = upgrade.TARGET_IMAGE
    target_raw["Image"] = manifest["target_image_id"]
    target_raw["Config"]["Labels"]["com.docker.compose.project.config_files"] = ",".join(
        [*(str(path.resolve()) for path in args.compose_file), str(args.override_file.resolve())]
    )
    target = migration.snapshot_from_inspect(target_raw)
    monkeypatch.setattr(migration, "wait_for_healthy", lambda *_: target)
    monkeypatch.setattr(migration, "prepare_compose_environment", lambda *_: None)
    monkeypatch.setattr(
        upgrade,
        "compare_target_semantics",
        lambda *_: {
            "source_semantic_match": True,
            "config_hash_match": False,
            "config_hash_drift_class": "COMPOSE_METADATA",
            "differing_fields": [],
            "live_config_hash": "a" * 64,
            "rendered_source_config_hash": "b" * 64,
        },
    )
    monkeypatch.setattr(upgrade, "require_compose_container_identity", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(migration, "protected_secret", lambda *_: "re_protected_test_secret")
    monkeypatch.setattr(migration, "secret_matches_container", lambda *_: None)

    class TargetRunner:
        def json(self, _command):
            return [target_raw]

    result = upgrade.validate_upgraded(TargetRunner(), args, manifest)
    assert result["container_id"] == target["container_id"]
    assert args._target_post_validation["semantic_report"]["config_hash_drift_class"] == (
        "COMPOSE_METADATA"
    )

    target["definition"]["image_id"] = "sha256:" + "f" * 64
    monkeypatch.setattr(migration, "wait_for_healthy", lambda *_: target)
    with pytest.raises(upgrade.LifecycleError, match="image identity"):
        upgrade.validate_upgraded(TargetRunner(), args, manifest)


def test_compose_hash_algorithm_drift_is_not_material_drift(tmp_path: Path) -> None:
    args, raw, compose = semantic_case(tmp_path, live_hash="1" * 64)
    report = upgrade.compare_source_semantics(
        SemanticRunner(raw, compose, rendered_hash="2" * 64),
        args,
        raw,
        migration.snapshot_from_inspect(raw),
    )
    assert report["config_hash_drift_class"] == "COMPOSE_METADATA"
    assert report["source_semantic_match"] is True


def test_lifecycle_label_drift_is_ignored(tmp_path: Path) -> None:
    args, raw, compose = semantic_case(tmp_path)
    raw["Config"]["Labels"].update(
        {
            "com.docker.compose.replace": "old-container",
            "com.docker.compose.version": "different-version",
        }
    )
    report = upgrade.compare_source_semantics(
        SemanticRunner(raw, compose),
        args,
        raw,
        migration.snapshot_from_inspect(raw),
    )
    assert report["source_semantic_match"] is True


def test_image_oci_and_docker_desktop_metadata_are_ignored(tmp_path: Path) -> None:
    args, raw, compose = semantic_case(tmp_path)
    raw["Config"]["Labels"].update(
        {
            "org.opencontainers.image.created": "2026-07-30T00:00:00Z",
            "org.opencontainers.image.revision": "opaque-revision",
            "desktop.docker.io/wsl-distro": "Ubuntu",
            "com.docker.compose.container-number": "1",
            "com.docker.compose.depends_on": "",
            "com.docker.compose.image": "opaque-image-id",
            "com.docker.compose.oneoff": "False",
            "com.docker.compose.replace": "old-container",
            "com.docker.compose.version": "2.39.1",
        }
    )
    report = upgrade.compare_source_semantics(
        SemanticRunner(raw, compose),
        args,
        raw,
        migration.snapshot_from_inspect(raw),
    )
    assert report["source_semantic_match"] is True
    assert report["differing_fields"] == []
    assert report["ignored_label_categories"] == [
        "com.docker.compose.*",
        "desktop.docker.io/*",
        "org.opencontainers.image.*",
    ]


@pytest.mark.parametrize(
    "label_key",
    [
        "com.docker.compose.config-hash",
        "com.docker.compose.container-number",
        "com.docker.compose.depends_on",
        "com.docker.compose.image",
        "com.docker.compose.oneoff",
        "com.docker.compose.replace",
        "com.docker.compose.version",
    ],
)
def test_observed_compose_lifecycle_labels_are_not_service_labels(
    tmp_path: Path,
    label_key: str,
) -> None:
    args, raw, compose = semantic_case(tmp_path)
    raw["Config"]["Labels"][label_key] = "runtime-only"
    report = upgrade.compare_source_semantics(
        SemanticRunner(raw, compose),
        args,
        raw,
        migration.snapshot_from_inspect(raw),
    )
    assert "labels" not in report["differing_fields"]
    assert report["source_semantic_match"] is True


@pytest.mark.parametrize(
    ("label_key", "expected_field"),
    [
        ("com.docker.compose.project", "compose_project"),
        ("com.docker.compose.service", "compose_service"),
    ],
)
def test_compose_project_and_service_identity_remain_strict(
    tmp_path: Path,
    label_key: str,
    expected_field: str,
) -> None:
    args, raw, compose = semantic_case(tmp_path)
    raw["Config"]["Labels"][label_key] = "wrong"
    report = upgrade.compare_source_semantics(
        SemanticRunner(raw, compose),
        args,
        raw,
        migration.snapshot_from_inspect(raw),
    )
    assert expected_field in report["differing_fields"]
    assert report["source_semantic_match"] is False


def test_explicit_configured_service_label_mismatch_fails(tmp_path: Path) -> None:
    args, raw, compose = semantic_case(tmp_path)
    compose["services"]["stalwart"]["labels"] = {"aiat.role": "mail-authority"}
    report = upgrade.compare_source_semantics(
        SemanticRunner(raw, compose),
        args,
        raw,
        migration.snapshot_from_inspect(raw),
    )
    assert "labels" in report["differing_fields"]
    assert report["source_semantic_match"] is False


def test_unknown_live_label_fails_closed(tmp_path: Path) -> None:
    args, raw, compose = semantic_case(tmp_path)
    raw["Config"]["Labels"]["aiat.unknown-runtime-label"] = "unknown"
    report = upgrade.compare_source_semantics(
        SemanticRunner(raw, compose),
        args,
        raw,
        migration.snapshot_from_inspect(raw),
    )
    assert "labels" in report["differing_fields"]
    assert report["source_semantic_match"] is False


def test_repository_change_is_recorded_when_semantics_still_match(
    tmp_path: Path,
) -> None:
    args, raw, compose = semantic_case(tmp_path)
    report = upgrade.compare_source_semantics(
        SemanticRunner(raw, compose, repository_changed=True),
        args,
        raw,
        migration.snapshot_from_inspect(raw),
    )
    assert report["config_hash_drift_class"] == "REPOSITORY_CHANGE"


@pytest.mark.parametrize(
    ("mutation", "field"),
    [
        (
            lambda _raw, compose: compose["services"]["stalwart"].update(
                command=["--different", "/etc/stalwart/config.toml"]
            ),
            "command",
        ),
        (
            lambda _raw, compose: compose["services"]["stalwart"].update(
                entrypoint=["/different-entrypoint"]
            ),
            "entrypoint",
        ),
        (
            lambda _raw, compose: compose["services"]["stalwart"][
                "environment"
            ].update(EXTRA_SECRET_SOURCE="configured"),
            "environment_names",
        ),
        (
            lambda _raw, compose: compose["services"]["stalwart"]["env_file"][0].update(
                path="/different/protected.env"
            ),
            "secret_override",
        ),
        (
            lambda _raw, compose: compose["services"]["stalwart"]["volumes"][0].update(
                target="/wrong"
            ),
            "mounts",
        ),
        (
            lambda _raw, compose: compose["services"]["stalwart"]["ports"][0].update(
                published="2526"
            ),
            "ports",
        ),
        (
            lambda _raw, compose: compose["services"]["stalwart"].update(
                networks={"public": None}
            ),
            "networks",
        ),
        (
            lambda _raw, compose: compose["services"]["stalwart"].update(
                security_opt=[]
            ),
            "security_opt",
        ),
        (
            lambda _raw, compose: compose["services"]["stalwart"]["healthcheck"].update(
                retries=9
            ),
            "healthcheck",
        ),
        (
            lambda _raw, compose: compose["services"]["stalwart"].update(user="1000"),
            "user",
        ),
    ],
)
def test_material_source_definition_changes_are_rejected(
    tmp_path: Path,
    mutation,
    field: str,
) -> None:
    args, raw, compose = semantic_case(tmp_path)
    mutation(raw, compose)
    report = upgrade.compare_source_semantics(
        SemanticRunner(raw, compose),
        args,
        raw,
        migration.snapshot_from_inspect(raw),
    )
    assert report["source_semantic_match"] is False
    assert report["config_hash_drift_class"] == "MATERIAL_DRIFT"
    assert field in report["differing_fields"]


def test_user_configured_label_drift_is_material(tmp_path: Path) -> None:
    args, raw, compose = semantic_case(tmp_path)
    raw["Config"]["Labels"]["aiat.security-policy"] = "strict"
    report = upgrade.compare_source_semantics(
        SemanticRunner(raw, compose),
        args,
        raw,
        migration.snapshot_from_inspect(raw),
    )
    assert "labels" in report["differing_fields"]


def test_target_override_never_participates_in_source_comparison(
    tmp_path: Path,
) -> None:
    args, raw, compose = semantic_case(tmp_path)
    runner = SemanticRunner(raw, compose)
    report = upgrade.compare_source_semantics(
        runner,
        args,
        raw,
        migration.snapshot_from_inspect(raw),
    )
    target_path = str(args.override_file)
    source_commands = [
        command for command in runner.commands if command[:2] == ["docker", "compose"]
    ]
    assert report["target_override_in_source_comparison"] is False
    assert all(target_path not in command for command in source_commands)


def test_diagnostic_output_is_bounded_and_never_contains_secret_or_fingerprint(
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "re_secret_that_must_not_be_printed"
    report = {
        "source_semantic_match": False,
        "config_hash_match": False,
        "config_hash_drift_class": "MATERIAL_DRIFT",
        "differing_fields": ["command", "environment_names"],
    }
    upgrade.print_source_report(report)
    output = capsys.readouterr().out
    assert secret not in output
    assert hashlib.sha256(secret.encode()).hexdigest() not in output
    assert "DIFFERING_FIELD=command" in output
    assert "IGNORED_LABEL_CATEGORIES=com.docker.compose.*,desktop.docker.io/*,org.opencontainers.image.*" in output
    assert "label-value" not in output
    assert len(output) < 2048


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
    runtime = {
        "snapshot": copy.deepcopy(value),
        "source_comparison": {
            "source_semantic_match": True,
            "config_hash_match": False,
            "config_hash_drift_class": "COMPOSE_METADATA",
            "differing_fields": [],
        },
    }
    monkeypatch.setattr(upgrade, "prepare_runtime", lambda *_: copy.deepcopy(runtime))
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
    monkeypatch.setattr(
        upgrade,
        "prepare_runtime",
        lambda *_: {
            "snapshot": copy.deepcopy(value),
            "source_comparison": {},
        },
    )
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


def test_cutover_source_validation_completes_before_stop(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = action_args(tmp_path)
    args.approve_security_upgrade = True
    args.backup_dir.mkdir(mode=0o700)
    manifest = manifest_for(source_snapshot())
    monkeypatch.setattr(upgrade, "require_root", lambda: None)
    monkeypatch.setattr(upgrade, "read_manifest", lambda *_: manifest)
    monkeypatch.setattr(upgrade, "read_backup_success", lambda *_: {})

    class NoMutationRunner:
        def __init__(self):
            self.commands: list[list[str]] = []

        def run(self, command, **_kwargs):
            self.commands.append(command)
            raise AssertionError(f"unexpected live command: {command}")

    runner = NoMutationRunner()

    def refused_runtime(*_args):
        raise migration.Refused("material source drift")

    monkeypatch.setattr(upgrade, "prepare_runtime", refused_runtime)
    with pytest.raises(migration.Refused, match="material source drift"):
        upgrade.cutover_action(runner, args)
    output = capsys.readouterr().out
    assert runner.commands == []
    assert "LIVE_MUTATION=NOT_PERFORMED" in output
    assert "SOURCE_STOP_INITIATED" not in output


class CutoverFailureRunner:
    def __init__(self, *, exit_code: int = 0, target_marker: str = ""):
        self.commands: list[list[str]] = []
        self.exit_code = exit_code
        self.target_marker = target_marker

    def run(self, command, **_kwargs):
        self.commands.append(command)
        if command[:2] == ["docker", "compose"] and "up" in command:
            if self.target_marker and self.target_marker in command:
                raise migration.Refused("simulated target recreation failure")
            return ""
        return ""

    def json(self, command):
        self.commands.append(command)
        if command[:3] == ["docker", "inspect", upgrade.CONTAINER]:
            raw = inspect_fixture()
            raw["State"] = {
                "Running": False,
                "ExitCode": self.exit_code,
                "OOMKilled": False,
                "Health": {"Status": "unhealthy"},
            }
            return [raw]
        raise AssertionError(f"unexpected JSON command: {command}")


def configure_cutover_failure_test(
    monkeypatch: pytest.MonkeyPatch,
    manifest: dict,
    source: dict,
) -> None:
    monkeypatch.setattr(upgrade, "require_root", lambda: None)
    monkeypatch.setattr(upgrade, "read_manifest", lambda *_: manifest)
    monkeypatch.setattr(upgrade, "read_backup_success", lambda *_: {})
    monkeypatch.setattr(
        upgrade,
        "prepare_runtime",
        lambda *_: {"snapshot": source, "source_comparison": {}},
    )
    monkeypatch.setattr(
        upgrade,
        "target_image_validation",
        lambda *_: (
            manifest["target_image_id"],
            {
                "target_repository_match": "PASS",
                "target_digest_match": "PASS",
                "target_platform": "linux/amd64",
            },
        ),
    )
    monkeypatch.setattr(
        migration,
        "compose_service_hash",
        lambda *_args, **_kwargs: manifest["target_compose_hash"],
    )
    monkeypatch.setattr(upgrade, "wait_for_source_healthy", lambda *_: source)
    monkeypatch.setattr(upgrade, "validate_recovered_source", lambda *_: source)


def test_cutover_uses_graceful_timeout_and_rolls_back_target_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = action_args(tmp_path)
    args.approve_security_upgrade = True
    args.backup_dir.mkdir(mode=0o700)
    source = source_snapshot()
    manifest = manifest_for(source)
    configure_cutover_failure_test(monkeypatch, manifest, source)
    prepare_calls: list[str] = []
    monkeypatch.setattr(
        upgrade,
        "prepare_runtime",
        lambda *_: (prepare_calls.append("pre-stop") or {"snapshot": source, "source_comparison": {}}),
    )
    runner = CutoverFailureRunner(target_marker=str(args.override_file))
    with pytest.raises(migration.Refused, match="source auto-recovery passed"):
        upgrade.cutover_action(runner, args)
    output = capsys.readouterr().out
    stop_index = runner.commands.index(
        ["docker", "stop", "--time", "45", upgrade.CONTAINER]
    )
    source_rollback = next(
        index
        for index, command in enumerate(runner.commands)
        if command[:2] == ["docker", "compose"]
        and "up" in command
        and str(args.override_file) not in command
    )
    assert stop_index < source_rollback
    assert "PRE_STOP_VALIDATION=PASS" in output
    assert "LIVE_MUTATION=PERFORMED" in output
    assert "SOURCE_AUTO_RECOVERY=PASS" in output
    assert "LIVE_MUTATION=NOT_PERFORMED" not in output
    assert prepare_calls == ["pre-stop"]
    assert (args.backup_dir / upgrade.CUTOVER_FAILURE_NAME).is_file()
    assert not any("volume" in part or "down" in part for command in runner.commands for part in command)


def test_sigkill_is_recorded_and_cutover_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = action_args(tmp_path)
    args.approve_security_upgrade = True
    args.backup_dir.mkdir(mode=0o700)
    source = source_snapshot()
    manifest = manifest_for(source)
    configure_cutover_failure_test(monkeypatch, manifest, source)
    runner = CutoverFailureRunner(exit_code=137, target_marker=str(args.override_file))
    with pytest.raises(migration.Refused, match="source auto-recovery passed"):
        upgrade.cutover_action(runner, args)
    output = capsys.readouterr().out
    assert "SOURCE_SIGKILL_REQUIRED=PASS" in output
    assert "SOURCE_AUTO_RECOVERY=PASS" in output
    assert not any(
        command[:2] == ["docker", "compose"] and str(args.override_file) in command
        for command in runner.commands
    )


def test_backup_integrity_is_read_only_and_reuses_existing_backup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = action_args(tmp_path)
    args.backup_dir.mkdir(mode=0o700)
    manifest = manifest_for(source_snapshot())
    monkeypatch.setattr(upgrade, "require_root", lambda: None)
    monkeypatch.setattr(upgrade, "read_manifest", lambda *_: manifest)
    monkeypatch.setattr(upgrade, "read_backup_success", lambda *_: {})

    class NoLiveCommands:
        def run(self, command, **_kwargs):
            raise AssertionError(f"backup integrity mutated live state: {command}")

    upgrade.backup_integrity_action(NoLiveCommands(), args)
    output = capsys.readouterr().out
    assert "BACKUP_INTEGRITY=PASS" in output
    assert "LIVE_MUTATION=NOT_PERFORMED" in output


def test_interrupted_pre_recreation_state_is_recovered_before_retry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    args = action_args(tmp_path)
    args.approve_security_upgrade = True
    args.backup_dir.mkdir(mode=0o700)
    source = source_snapshot()
    manifest = manifest_for(source)
    upgrade.cutover_state_path(args, upgrade.SOURCE_STOP_INITIATED_NAME).write_text(
        "{}\n", encoding="utf-8"
    )
    configure_cutover_failure_test(monkeypatch, manifest, source)
    monkeypatch.setattr(
        migration,
        "inspect_container",
        lambda *_: {
            **source,
            "running": False,
            "health": "none",
        },
    )
    monkeypatch.setattr(upgrade, "recover_source", lambda *_args, **_kwargs: {"source_auto_recovery": "PASS"})
    def validate_target(_runner, target_args, _manifest):
        target_args._target_post_validation = {
            "health": "PASS",
            "semantic_report": {"config_hash_drift_class": "COMPOSE_METADATA"},
        }
        return source

    monkeypatch.setattr(upgrade, "validate_upgraded", validate_target)
    monkeypatch.setattr(upgrade, "write_cutover_success", lambda *_args, **_kwargs: None)

    class SuccessfulRunner:
        def run(self, command, **_kwargs):
            return ""

        def json(self, _command):
            raw = inspect_fixture()
            raw["State"] = {
                "Running": False,
                "ExitCode": 0,
                "OOMKilled": False,
                "Health": {"Status": "none"},
            }
            return [raw]

    upgrade.cutover_action(SuccessfulRunner(), args)
    assert upgrade.cutover_state_path(args, upgrade.PRE_STOP_VALIDATION_NAME).is_file()


def safe_failure_artifact(*, target_created: bool = False) -> dict:
    return {
        "schema": 1,
        "container": upgrade.CONTAINER,
        "last_completed_phase": "SOURCE_STOPPED",
        "live_mutation": "PERFORMED",
        "source_auto_recovery": "PASS",
        "recovery_method": "docker-start",
        "failed_at": "2026-07-30T00:00:00Z",
        "secret_or_fingerprint_stored": False,
        "volumes_deleted_or_recreated": False,
        "failure_stage": "TARGET_RECREATION_COMMAND",
        "exception_type": "CommandError",
        "error_code": "compose-command-failure",
        "sanitized_message": "Compose command failed",
        "command_executable_subcommand": "docker compose",
        "return_code": 1,
        "target_container_created": target_created,
        "target_became_healthy": False,
        "target_container_id": "e" * 64 if target_created else "",
    }


def test_failed_cutover_artifact_is_sanitized_and_phase_accurate(
    tmp_path: Path,
) -> None:
    args = action_args(tmp_path)
    args.backup_dir.mkdir(mode=0o700)
    secret = "re_secret_that_must_not_be_recorded"
    exc = upgrade.CommandError(
        f"raw stderr {secret}",
        code="compose-command-failure",
        command=["docker", "compose", "--env", secret, "up"],
        return_code=17,
    )
    upgrade.record_cutover_failure(
        args,
        last_completed_phase="SOURCE_STOPPED",
        mutation=True,
        recovery={"source_auto_recovery": "PASS", "recovery_method": "docker-start"},
        exc=exc,
        target_recreation_started=True,
        target_evidence={
            "target_container_created": False,
            "target_became_healthy": False,
            "target_container_id": "",
        },
    )
    content = upgrade.cutover_state_path(args, upgrade.CUTOVER_FAILURE_NAME).read_text(
        encoding="utf-8"
    )
    assert secret not in content
    assert hashlib.sha256(secret.encode()).hexdigest() not in content
    artifact = json.loads(content)
    assert artifact["failure_stage"] == "TARGET_RECREATION_COMMAND"
    assert artifact["error_code"] == "compose-command-failure"
    assert artifact["command_executable_subcommand"] == "docker compose"
    assert artifact["return_code"] == 17


def test_failure_diagnose_is_read_only_and_reports_recovered_source(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = action_args(tmp_path)
    args.backup_dir.mkdir(mode=0o700)
    manifest = manifest_for(source_snapshot())
    upgrade.cutover_state_path(args, upgrade.CUTOVER_FAILURE_NAME).write_text(
        json.dumps(safe_failure_artifact()) + "\n", encoding="utf-8"
    )
    monkeypatch.setattr(upgrade, "require_root", lambda: None)
    monkeypatch.setattr(upgrade, "read_manifest", lambda *_: manifest)
    monkeypatch.setattr(upgrade, "read_backup_success", lambda *_: {})
    monkeypatch.setattr(upgrade, "validate_recovered_source", lambda *_: manifest["source"])

    class NoLiveCommands:
        def run(self, command, **_kwargs):
            raise AssertionError(f"failure diagnose mutated live state: {command}")

        def json(self, command):
            raise AssertionError(f"failure diagnose inspected live state: {command}")

    upgrade.failure_diagnose_action(NoLiveCommands(), args)
    output = capsys.readouterr().out
    assert "FAILURE_DIAGNOSIS=PASS" in output
    assert "SOURCE_RECOVERED=PASS" in output
    assert "SECRET_VALUE_OR_FINGERPRINT_OUTPUT=NONE" in output
    assert "Compose command failed" in output


def test_governed_retry_archives_previous_attempt_without_overwrite(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = action_args(tmp_path)
    args.approve_security_upgrade = True
    args.backup_dir.mkdir(mode=0o700)
    manifest = manifest_for(source_snapshot())
    for name, value in (
        (upgrade.CUTOVER_FAILURE_NAME, safe_failure_artifact()),
        (upgrade.TARGET_RECREATION_INITIATED_NAME, {}),
    ):
        path = upgrade.cutover_state_path(args, name)
        path.write_text(json.dumps(value) + "\n", encoding="utf-8")
        path.chmod(0o600)
    monkeypatch.setattr(upgrade, "require_root", lambda: None)
    monkeypatch.setattr(upgrade, "read_manifest", lambda *_: manifest)
    monkeypatch.setattr(upgrade, "read_backup_success", lambda *_: {})
    monkeypatch.setattr(
        upgrade,
        "prepare_runtime",
        lambda *_: {"snapshot": manifest["source"], "source_comparison": {}},
    )
    monkeypatch.setattr(
        upgrade,
        "target_image_validation",
        lambda *_: (manifest["target_image_id"], manifest["target_image_validation"]),
    )
    monkeypatch.setattr(upgrade, "validate_no_target_container", lambda *_: None)
    invoked: list[str] = []
    monkeypatch.setattr(upgrade, "cutover_action", lambda *_: invoked.append("cutover"))

    upgrade.retry_action(SimpleNamespace(), args)
    output = capsys.readouterr().out
    archive_dirs = list((args.backup_dir / upgrade.ATTEMPT_HISTORY_DIR).iterdir())
    assert len(archive_dirs) == 1
    assert (archive_dirs[0] / upgrade.CUTOVER_FAILURE_NAME).is_file()
    assert (archive_dirs[0] / upgrade.TARGET_RECREATION_INITIATED_NAME).is_file()
    assert not upgrade.cutover_state_path(args, upgrade.CUTOVER_FAILURE_NAME).exists()
    assert invoked == ["cutover"]
    assert "GOVERNED_RETRY=AUTHORIZED" in output
    assert "secret" not in output.lower()


def test_governed_retry_refuses_when_prior_target_exists(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    args = action_args(tmp_path)
    args.approve_security_upgrade = True
    args.backup_dir.mkdir(mode=0o700)
    manifest = manifest_for(source_snapshot())
    artifact = safe_failure_artifact(target_created=True)
    upgrade.cutover_state_path(args, upgrade.CUTOVER_FAILURE_NAME).write_text(
        json.dumps(artifact) + "\n", encoding="utf-8"
    )
    monkeypatch.setattr(upgrade, "require_root", lambda: None)
    monkeypatch.setattr(upgrade, "read_manifest", lambda *_: manifest)
    monkeypatch.setattr(upgrade, "read_backup_success", lambda *_: {})
    with pytest.raises(migration.Refused, match="created a target"):
        upgrade.retry_action(SimpleNamespace(), args)


def legacy_failure_for_adoption() -> dict:
    return {
        "schema": 1,
        "container": upgrade.CONTAINER,
        "last_completed_phase": "TARGET_RECREATION",
        "live_mutation": "PERFORMED",
        "source_auto_recovery": "PASS",
        "recovery_method": "docker-start",
        "failed_at": "2026-07-30T00:00:00Z",
        "secret_or_fingerprint_stored": False,
        "volumes_deleted_or_recreated": False,
    }


def target_recreation_for_adoption() -> dict:
    return {
        "schema": 1,
        "container": upgrade.CONTAINER,
        "initiated_at": "2026-07-30T00:00:00Z",
        "live_mutation": "PERFORMED",
    }


def write_protected_test_artifact(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
    path.chmod(0o600)


def configure_legacy_adoption_test(
    monkeypatch: pytest.MonkeyPatch,
    args: SimpleNamespace,
    manifest: dict,
    source: dict,
) -> None:
    report = {
        "source_semantic_match": True,
        "config_hash_match": False,
        "config_hash_drift_class": "COMPOSE_METADATA",
        "differing_fields": [],
    }
    target_report = manifest["target_image_validation"]
    monkeypatch.setattr(upgrade, "require_root", lambda: None)
    monkeypatch.setattr(upgrade, "read_manifest", lambda *_: manifest)
    monkeypatch.setattr(upgrade, "read_backup_success", lambda *_: {})
    monkeypatch.setattr(
        upgrade,
        "validate_legacy_current_state",
        lambda *_: (source, report, target_report),
    )


def test_legacy_failure_adoption_is_idempotent_and_secret_free(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = action_args(tmp_path)
    args.backup_dir.mkdir(mode=0o700)
    source = source_snapshot()
    manifest = manifest_for(source)
    write_protected_test_artifact(
        upgrade.cutover_state_path(args, upgrade.CUTOVER_FAILURE_NAME),
        legacy_failure_for_adoption(),
    )
    write_protected_test_artifact(
        upgrade.cutover_state_path(args, upgrade.TARGET_RECREATION_INITIATED_NAME),
        target_recreation_for_adoption(),
    )
    configure_legacy_adoption_test(monkeypatch, args, manifest, source)

    class NoLiveMutation:
        def run(self, command, **_kwargs):
            raise AssertionError(f"legacy adoption mutated live state: {command}")

        def json(self, command):
            raise AssertionError(f"legacy adoption inspected live state: {command}")

    failure_path = upgrade.cutover_state_path(args, upgrade.CUTOVER_FAILURE_NAME)
    target_path = upgrade.cutover_state_path(args, upgrade.TARGET_RECREATION_INITIATED_NAME)
    original_failure = failure_path.read_bytes()
    original_target = target_path.read_bytes()
    upgrade.adopt_legacy_failure_action(NoLiveMutation(), args)
    first_output = capsys.readouterr().out
    upgrade.adopt_legacy_failure_action(NoLiveMutation(), args)
    second_output = capsys.readouterr().out
    adoption = json.loads(upgrade.legacy_adoption_path(args).read_text(encoding="utf-8"))
    assert "LEGACY_FAILURE_ADOPTION=PASS" in first_output
    assert "ADOPTION_ALREADY_VERIFIED=PASS" in second_output
    assert "GOVERNED_RETRY_ELIGIBLE=PASS" in second_output
    assert "re_protected_test_secret" not in first_output + second_output
    assert hashlib.sha256(b"re_protected_test_secret").hexdigest() not in (
        first_output + second_output
    )
    assert failure_path.read_bytes() == original_failure
    assert target_path.read_bytes() == original_target
    archive = upgrade.legacy_adoption_archive_path(args)
    assert (archive / upgrade.CUTOVER_FAILURE_NAME).read_bytes() == original_failure
    assert (archive / upgrade.TARGET_RECREATION_INITIATED_NAME).read_bytes() == original_target
    assert adoption["live_mutation"] == "NOT_PERFORMED"
    assert adoption["secret_value_or_fingerprint_stored"] is False
    monkeypatch.setattr(upgrade, "validate_recovered_source", lambda *_: source)
    upgrade.failure_diagnose_action(NoLiveMutation(), args)
    diagnosis_output = capsys.readouterr().out
    assert "FAILURE_DIAGNOSIS=PASS" in diagnosis_output
    assert "LEGACY_FAILURE_ADOPTION=PASS" in diagnosis_output
    assert "GOVERNED_RETRY_ELIGIBLE=PASS" in diagnosis_output


def test_legacy_adoption_requires_recorded_recovery_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    args = action_args(tmp_path)
    args.backup_dir.mkdir(mode=0o700)
    broken = legacy_failure_for_adoption()
    broken.pop("source_auto_recovery")
    write_protected_test_artifact(
        upgrade.cutover_state_path(args, upgrade.CUTOVER_FAILURE_NAME), broken
    )
    with pytest.raises(migration.Refused, match="sufficient recovery evidence"):
        upgrade.read_legacy_failure(args)


def configure_recreated_source_validation(
    monkeypatch: pytest.MonkeyPatch,
    manifest: dict,
    recovered: dict,
    *,
    report_class: str = "COMPOSE_METADATA",
) -> None:
    report = {
        "source_semantic_match": True,
        "config_hash_match": report_class == "NONE",
        "config_hash_drift_class": report_class,
        "differing_fields": [],
    }
    monkeypatch.setattr(
        upgrade,
        "prepare_runtime",
        lambda *_: {"snapshot": recovered, "source_comparison": report},
    )
    monkeypatch.setattr(upgrade, "validate_no_target_container", lambda *_: None)
    monkeypatch.setattr(
        upgrade,
        "target_image_validation",
        lambda *_: (manifest["target_image_id"], manifest["target_image_validation"]),
    )


def write_legacy_recovery_evidence(args: SimpleNamespace) -> None:
    write_protected_test_artifact(
        upgrade.cutover_state_path(args, upgrade.CUTOVER_FAILURE_NAME),
        legacy_failure_for_adoption(),
    )
    write_protected_test_artifact(
        upgrade.cutover_state_path(args, upgrade.TARGET_RECREATION_INITIATED_NAME),
        target_recreation_for_adoption(),
    )


def test_recreated_source_id_is_accepted_with_verified_recovery_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    args = action_args(tmp_path)
    args.backup_dir.mkdir(mode=0o700)
    original, recovered = recovered_source_pair()
    manifest = manifest_for(original)
    write_legacy_recovery_evidence(args)
    configure_recreated_source_validation(monkeypatch, manifest, recovered)

    current, report, _target_report = upgrade.validate_legacy_current_state(
        SimpleNamespace(), args, manifest
    )
    assert current["container_id"] == RECOVERED_SOURCE_ID
    assert current["definition"] == original["definition"]
    assert report["config_hash_drift_class"] == "COMPOSE_METADATA"


def test_recreated_source_id_is_refused_without_recovery_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    args = action_args(tmp_path)
    args.backup_dir.mkdir(mode=0o700)
    original, recovered = recovered_source_pair()
    manifest = manifest_for(original)
    configure_recreated_source_validation(monkeypatch, manifest, recovered)
    with pytest.raises(
        upgrade.RecoveryIdentityError,
        match="source-recovery-identity-unverified",
    ):
        upgrade.validate_legacy_current_state(SimpleNamespace(), args, manifest)


def test_recreated_source_id_requires_target_recreation_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    args = action_args(tmp_path)
    args.backup_dir.mkdir(mode=0o700)
    original, recovered = recovered_source_pair()
    manifest = manifest_for(original)
    write_protected_test_artifact(
        upgrade.cutover_state_path(args, upgrade.CUTOVER_FAILURE_NAME),
        legacy_failure_for_adoption(),
    )
    configure_recreated_source_validation(monkeypatch, manifest, recovered)
    with pytest.raises(
        upgrade.RecoveryIdentityError,
        match="source-recovery-identity-unverified",
    ):
        upgrade.validate_legacy_current_state(SimpleNamespace(), args, manifest)


def test_failure_diagnose_reports_stable_identity_error_without_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = action_args(tmp_path)
    args.backup_dir.mkdir(mode=0o700)
    original, recovered = recovered_source_pair()
    manifest = manifest_for(original)
    write_protected_test_artifact(
        upgrade.cutover_state_path(args, upgrade.CUTOVER_FAILURE_NAME),
        legacy_failure_for_adoption(),
    )
    configure_recreated_source_validation(monkeypatch, manifest, recovered)
    monkeypatch.setattr(upgrade, "require_root", lambda: None)
    monkeypatch.setattr(upgrade, "read_manifest", lambda *_: manifest)
    monkeypatch.setattr(upgrade, "read_backup_success", lambda *_: {})
    with pytest.raises(migration.Refused, match="not currently adoption-safe"):
        upgrade.failure_diagnose_action(SimpleNamespace(), args)
    output = capsys.readouterr().out
    assert "SOURCE_RECOVERED=FAIL" in output
    assert "ERROR_CODE=source-recovery-identity-unverified" in output
    assert "re_protected_test_secret" not in output
    assert hashlib.sha256(b"re_protected_test_secret").hexdigest() not in output


@pytest.mark.parametrize(
    "field, mutate",
    [
        ("image_ref", lambda value: upgrade.TARGET_IMAGE),
        ("mounts", lambda value: [*value[:-1], {**value[-1], "rw": False}]),
        (
            "mounts",
            lambda value: [*value[:-1], {**value[-1], "propagation": "rprivate"}],
        ),
        ("ports", lambda value: {**value, "25/tcp": [{"host_ip": "0.0.0.0", "host_port": "2525"}]}),
        ("networks", lambda value: ["mas_internal"]),
        ("restart_policy", lambda value: {"Name": "always", "MaximumRetryCount": 0}),
        ("security_opt", lambda value: []),
        ("cmd_hash", lambda value: "f" * 64),
    ],
)
def test_recreated_source_definition_drift_is_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    field: str,
    mutate,
) -> None:
    args = action_args(tmp_path)
    args.backup_dir.mkdir(mode=0o700)
    original, recovered = recovered_source_pair()
    manifest = manifest_for(original)
    write_legacy_recovery_evidence(args)
    recovered["definition"][field] = mutate(recovered["definition"][field])
    configure_recreated_source_validation(monkeypatch, manifest, recovered)
    with pytest.raises(
        upgrade.RecoveryIdentityError,
        match="source-recovery-identity-unverified",
    ):
        upgrade.validate_legacy_current_state(SimpleNamespace(), args, manifest)


def test_recreated_source_metadata_hash_drift_is_recorded_not_rejected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    args = action_args(tmp_path)
    args.backup_dir.mkdir(mode=0o700)
    original, recovered = recovered_source_pair()
    recovered["compose"]["config_hash"] = "d" * 64
    manifest = manifest_for(original)
    write_legacy_recovery_evidence(args)
    configure_recreated_source_validation(monkeypatch, manifest, recovered, report_class="NONE")
    _current, report, _target_report = upgrade.validate_legacy_current_state(
        SimpleNamespace(), args, manifest
    )
    assert report["config_hash_drift_class"] == "COMPOSE_METADATA"


def test_recreated_source_adoption_binds_recovered_id_and_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = action_args(tmp_path)
    args.backup_dir.mkdir(mode=0o700)
    original, recovered = recovered_source_pair()
    manifest = manifest_for(original)
    write_legacy_recovery_evidence(args)
    configure_recreated_source_validation(monkeypatch, manifest, recovered)
    monkeypatch.setattr(upgrade, "require_root", lambda: None)
    monkeypatch.setattr(upgrade, "read_manifest", lambda *_: manifest)
    monkeypatch.setattr(upgrade, "read_backup_success", lambda *_: {})

    class NoLiveMutation:
        def run(self, command, **_kwargs):
            raise AssertionError(f"adoption mutated live state: {command}")

        def json(self, command):
            raise AssertionError(f"adoption inspected live state: {command}")

    upgrade.adopt_legacy_failure_action(NoLiveMutation(), args)
    first = capsys.readouterr().out
    upgrade.adopt_legacy_failure_action(NoLiveMutation(), args)
    second = capsys.readouterr().out
    artifact = json.loads(upgrade.legacy_adoption_path(args).read_text(encoding="utf-8"))
    assert artifact["original_source_container_id"] == ORIGINAL_RECOVERY_SOURCE_ID
    assert artifact["recovered_source_container_id"] == RECOVERED_SOURCE_ID
    assert artifact["source_container_recreated"] is True
    assert artifact["source_container_id"] == RECOVERED_SOURCE_ID
    assert "LEGACY_FAILURE_ADOPTION=PASS" in first
    assert "ADOPTION_ALREADY_VERIFIED=PASS" in second
    assert "SECRET_VALUE_OR_FINGERPRINT_OUTPUT=NONE" in second
    upgrade.failure_diagnose_action(NoLiveMutation(), args)
    diagnosis = capsys.readouterr().out
    assert "SOURCE_RECOVERED=PASS" in diagnosis
    assert "SOURCE_CONTAINER_RECREATED=PASS" in diagnosis
    assert f"ORIGINAL_SOURCE_CONTAINER_ID={ORIGINAL_RECOVERY_SOURCE_ID}" in diagnosis
    assert f"RECOVERED_SOURCE_CONTAINER_ID={RECOVERED_SOURCE_ID}" in diagnosis
    assert "GOVERNED_RETRY_ELIGIBLE=PASS" in diagnosis


def test_governed_retry_accepts_recreated_source_after_adoption(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    args = action_args(tmp_path)
    args.approve_security_upgrade = True
    args.backup_dir.mkdir(mode=0o700)
    original, recovered = recovered_source_pair()
    manifest = manifest_for(original)
    write_legacy_recovery_evidence(args)
    configure_recreated_source_validation(monkeypatch, manifest, recovered)
    monkeypatch.setattr(upgrade, "require_root", lambda: None)
    monkeypatch.setattr(upgrade, "read_manifest", lambda *_: manifest)
    monkeypatch.setattr(upgrade, "read_backup_success", lambda *_: {})
    upgrade.adopt_legacy_failure_action(SimpleNamespace(), args)

    seen: list[dict] = []
    monkeypatch.setattr(
        upgrade,
        "cutover_action",
        lambda _runner, retry_args: seen.append(
            getattr(retry_args, "_legacy_recovery_artifact", {})
        ),
    )
    upgrade.retry_action(SimpleNamespace(), args)
    assert len(seen) == 1
    assert seen[0]["recovered_source_container_id"] == RECOVERED_SOURCE_ID
    current, report, target_report = upgrade.validate_legacy_current_state(
        SimpleNamespace(), args, manifest, recovery_artifact=seen[0]
    )
    upgrade.validate_adoption_artifact(
        SimpleNamespace(),
        args,
        manifest,
        seen[0],
        current,
        report,
        target_report,
    )


@pytest.mark.parametrize(
    "reason",
    [
        "current v0.16.7 source does not match",
        "not healthy",
        "source semantic validation is not adoption-safe",
        "secret source mismatch",
        "v0.16.15 target container still exists",
    ],
)
def test_legacy_adoption_refuses_current_state_drift(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    reason: str,
) -> None:
    args = action_args(tmp_path)
    args.backup_dir.mkdir(mode=0o700)
    source = source_snapshot()
    manifest = manifest_for(source)
    monkeypatch.setattr(
        upgrade,
        "prepare_runtime",
        lambda *_: (_ for _ in ()).throw(migration.Refused(reason)),
    )
    with pytest.raises(migration.Refused, match=reason):
        upgrade.validate_legacy_current_state(SimpleNamespace(), args, manifest)


def test_legacy_adoption_rejects_invalid_backup_and_completed_cutover(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    args = action_args(tmp_path)
    args.backup_dir.mkdir(mode=0o700)
    source = source_snapshot()
    manifest = manifest_for(source)
    monkeypatch.setattr(upgrade, "require_root", lambda: None)
    monkeypatch.setattr(upgrade, "read_manifest", lambda *_: manifest)
    monkeypatch.setattr(
        upgrade,
        "read_backup_success",
        lambda *_: (_ for _ in ()).throw(migration.Refused("invalid backup")),
    )
    with pytest.raises(migration.Refused, match="invalid backup"):
        upgrade.adopt_legacy_failure_action(SimpleNamespace(), args)

    monkeypatch.setattr(upgrade, "read_backup_success", lambda *_: {})
    upgrade.cutover_success_path(args).write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(upgrade, "read_legacy_failure", lambda *_: legacy_failure_for_adoption())
    monkeypatch.setattr(
        upgrade,
        "read_target_recreation_artifact",
        lambda *_: target_recreation_for_adoption(),
    )
    with pytest.raises(migration.Refused, match="completed cutover"):
        upgrade.adopt_legacy_failure_action(SimpleNamespace(), args)


def test_legacy_adoption_artifact_tampering_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    args = action_args(tmp_path)
    args.backup_dir.mkdir(mode=0o700)
    source = source_snapshot()
    manifest = manifest_for(source)
    write_protected_test_artifact(
        upgrade.cutover_state_path(args, upgrade.CUTOVER_FAILURE_NAME),
        legacy_failure_for_adoption(),
    )
    write_protected_test_artifact(
        upgrade.cutover_state_path(args, upgrade.TARGET_RECREATION_INITIATED_NAME),
        target_recreation_for_adoption(),
    )
    configure_legacy_adoption_test(monkeypatch, args, manifest, source)
    upgrade.adopt_legacy_failure_action(SimpleNamespace(), args)
    (upgrade.legacy_adoption_archive_path(args) / upgrade.CUTOVER_FAILURE_NAME).write_text(
        "tampered\n", encoding="utf-8"
    )
    (upgrade.legacy_adoption_archive_path(args) / upgrade.CUTOVER_FAILURE_NAME).chmod(0o600)
    with pytest.raises(migration.Refused, match="tampered"):
        upgrade.adopt_legacy_failure_action(SimpleNamespace(), args)


def test_governed_retry_accepts_adopted_legacy_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    args = action_args(tmp_path)
    args.approve_security_upgrade = True
    args.backup_dir.mkdir(mode=0o700)
    source = source_snapshot()
    manifest = manifest_for(source)
    write_protected_test_artifact(
        upgrade.cutover_state_path(args, upgrade.CUTOVER_FAILURE_NAME),
        legacy_failure_for_adoption(),
    )
    write_protected_test_artifact(
        upgrade.cutover_state_path(args, upgrade.TARGET_RECREATION_INITIATED_NAME),
        target_recreation_for_adoption(),
    )
    configure_legacy_adoption_test(monkeypatch, args, manifest, source)
    upgrade.adopt_legacy_failure_action(SimpleNamespace(), args)
    monkeypatch.setattr(
        upgrade,
        "prepare_runtime",
        lambda *_: {"snapshot": source, "source_comparison": {}},
    )
    monkeypatch.setattr(
        upgrade,
        "target_image_validation",
        lambda *_: (manifest["target_image_id"], manifest["target_image_validation"]),
    )
    monkeypatch.setattr(upgrade, "validate_no_target_container", lambda *_: None)
    invoked: list[str] = []
    monkeypatch.setattr(upgrade, "cutover_action", lambda *_: invoked.append("cutover"))
    upgrade.retry_action(SimpleNamespace(), args)
    assert invoked == ["cutover"]
    assert upgrade.backup_success_path(args).exists() is False


def test_target_absence_check_is_read_only(tmp_path: Path) -> None:
    args = action_args(tmp_path)
    current = source_snapshot()
    commands: list[list[str]] = []

    class ReadOnlyRunner:
        def run(self, command, **_kwargs):
            commands.append(command)
            if "ancestor=" in " ".join(command):
                return ""
            return current["container_id"] + "\n"

        def json(self, command):
            commands.append(command)
            return [inspect_fixture()]

    upgrade.validate_no_target_container(ReadOnlyRunner(), args, current)
    assert all(
        not any(token in command for token in ("stop", "start", "up", "rm", "volume"))
        for command in commands
    )


def test_upgrade_source_contains_no_volume_delete_or_compose_down() -> None:
    source = (SCRIPTS / "stalwart_security_upgrade.py").read_text(encoding="utf-8")
    assert "docker volume rm" not in source
    assert "docker compose down" not in source
    assert "down -v" not in source
