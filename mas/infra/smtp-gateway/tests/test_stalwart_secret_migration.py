from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[4]
SCRIPT = ROOT / "mas" / "infra" / "smtp-gateway" / "scripts" / "stalwart_secret_migration.py"
SPEC = importlib.util.spec_from_file_location("stalwart_secret_migration", SCRIPT)
assert SPEC and SPEC.loader
migration = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(migration)


PINNED_IMAGE = (
    "ghcr.io/stalwartlabs/stalwart:v0.16.7@"
    "sha256:6a8ddaa5728a5e78a8611085069f63414cd43c3a669471785dd41aad1ca16e63"
)


def inspect_fixture(*, secret: str | None = None) -> dict:
    environment = ["PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin"]
    if secret is not None:
        environment.append(f"RESEND_API_KEY={secret}")
    return {
        "Id": "a" * 64,
        "Name": "/mas-stalwart-1",
        "Image": "sha256:" + "b" * 64,
        "Config": {
            "Image": PINNED_IMAGE,
            "Hostname": "stalwart",
            "User": "",
            "Entrypoint": ["/usr/local/bin/stalwart-mail"],
            "Cmd": ["--config", "/etc/stalwart/config.toml"],
            "WorkingDir": "/opt/stalwart",
            "ExposedPorts": {"25/tcp": {}, "8080/tcp": {}},
            "Healthcheck": {"Test": ["CMD", "curl", "-f", "http://127.0.0.1:8080/healthz/ready"]},
            "Env": environment,
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


def snapshot(*, secret: str | None = None) -> dict:
    return migration.snapshot_from_inspect(inspect_fixture(secret=secret))


def test_missing_resend_api_key_is_rejected_when_injection_is_required() -> None:
    with pytest.raises(migration.Refused, match="RESEND_API_KEY must be present"):
        migration.validate_snapshot(
            snapshot(),
            persistent_target="/var/lib/stalwart",
            require_secret=True,
        )


def test_unpinned_image_is_rejected() -> None:
    value = snapshot()
    value["definition"]["image_ref"] = "ghcr.io/stalwartlabs/stalwart:v0.16.7"
    with pytest.raises(migration.Refused, match="not pinned by digest"):
        migration.validate_snapshot(
            value,
            persistent_target="/var/lib/stalwart",
            require_secret=False,
        )


def test_unidentified_data_mount_is_rejected() -> None:
    value = snapshot()
    value["definition"]["mounts"] = [
        item for item in value["definition"]["mounts"]
        if item["destination"] != "/var/lib/stalwart"
    ]
    with pytest.raises(migration.Refused, match="cannot be identified"):
        migration.validate_snapshot(
            value,
            persistent_target="/var/lib/stalwart",
            require_secret=False,
        )


def test_anonymous_or_untracked_volume_is_rejected() -> None:
    class VolumeRunner:
        def json(self, _args):
            return [{"Labels": {}}]

    value = snapshot()
    with pytest.raises(migration.Refused, match="not tracked by Compose"):
        migration.validate_mount_tracking(VolumeRunner(), value, project="mas")


@pytest.mark.parametrize(
    ("field", "mutation", "message"),
    [
        (
            "mounts",
            lambda value: value[1].update(
                source="/var/lib/docker/volumes/replacement/_data",
                name="replacement",
            ),
            "volume or mount source changed",
        ),
        (
            "ports",
            lambda value: value["25/tcp"][0].update(host_port="2526"),
            "published ports changed",
        ),
        (
            "networks",
            lambda value: value.append("untrusted"),
            "container networks changed",
        ),
    ],
)
def test_preservation_rejects_changed_runtime_definition(field, mutation, message) -> None:
    before = snapshot()
    after = snapshot(secret="re_test_secret_value_long_enough")
    mutation(after["definition"][field])
    after["definition_fingerprint"] = migration.canonical_hash(after["definition"])
    with pytest.raises(migration.Refused, match=message):
        migration.compare_preserved(before, after, expect_secret=True)


def test_sanitized_manifest_never_contains_secret_or_environment_values() -> None:
    secret = "re_live_secret_that_must_never_leak"
    value = snapshot(secret=secret)
    serialized = json.dumps(value, sort_keys=True)
    assert secret not in serialized
    assert "RESEND_API_KEY=" not in serialized
    assert value["resend_secret_present"] is True
    source = SCRIPT.read_text(encoding="utf-8")
    assert "docker compose down" not in source
    assert "docker volume rm" not in source
    assert "RESEND_API_KEY={secret}" not in source


def test_successful_recreation_preserves_definition_and_adds_only_secret() -> None:
    before = snapshot()
    after_raw = inspect_fixture(secret="re_test_secret_value_long_enough")
    after_raw["Id"] = "d" * 64
    after_raw["Config"]["Labels"]["com.docker.compose.config-hash"] = "e" * 64
    after = migration.snapshot_from_inspect(after_raw)
    migration.compare_preserved(before, after, expect_secret=True)
    assert before["container_id"] != after["container_id"]


def test_rollback_preserves_definition_and_removes_secret() -> None:
    original = snapshot()
    restored_raw = inspect_fixture()
    restored_raw["Id"] = "f" * 64
    restored = migration.snapshot_from_inspect(restored_raw)
    migration.compare_preserved(original, restored, expect_secret=False)
    assert restored["resend_secret_present"] is False
