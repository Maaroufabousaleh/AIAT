from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

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


def test_legacy_hashed_labels_compare_with_raw_label_values() -> None:
    before = snapshot()
    after = snapshot(secret="re_test_secret_value_long_enough")
    after["definition"]["labels"] = {
        "com.docker.compose.project": "mas",
        "com.docker.compose.service": "stalwart",
        "aiat.role": "mail-authority",
    }
    migration.compare_preserved(before, after, expect_secret=True)


def test_expected_compose_metadata_label_changes_are_ignored() -> None:
    before_raw = inspect_fixture()
    before_raw["Config"]["Labels"]["com.docker.compose.project.config_files"] = "base.yml"
    after_raw = inspect_fixture(secret="re_test_secret_value_long_enough")
    after_raw["Config"]["Labels"].update(
        {
            "com.docker.compose.config-hash": "d" * 64,
            "com.docker.compose.project.config_files": "base.yml,secret.yml",
            "com.docker.compose.replace": "old-container",
        }
    )
    migration.compare_preserved(
        migration.snapshot_from_inspect(before_raw),
        migration.snapshot_from_inspect(after_raw),
        expect_secret=True,
    )


def test_unexpected_configured_label_change_is_rejected() -> None:
    before = snapshot()
    after_raw = inspect_fixture(secret="re_test_secret_value_long_enough")
    after_raw["Config"]["Labels"]["aiat.role"] = "changed"
    after = migration.snapshot_from_inspect(after_raw)
    with pytest.raises(migration.Refused, match="configured labels changed"):
        migration.compare_preserved(before, after, expect_secret=True)


def test_already_recreated_healthy_state_is_recoverable() -> None:
    before = snapshot()
    after_raw = inspect_fixture(secret="re_test_secret_value_long_enough")
    after_raw["Id"] = "d" * 64
    after = migration.snapshot_from_inspect(after_raw)
    migration.validate_recovery_state(before, after)


@pytest.mark.parametrize(
    ("field", "mutation", "message"),
    [
        ("image_ref", lambda value: value.update(image_ref="bad"), "image digest changed"),
        (
            "mounts",
            lambda value: value["mounts"][1].update(source="/changed"),
            "volume or mount source changed",
        ),
        (
            "ports",
            lambda value: value["ports"]["25/tcp"][0].update(host_port="9999"),
            "published ports changed",
        ),
        (
            "networks",
            lambda value: value["networks"].append("wrong"),
            "container networks changed",
        ),
    ],
)
def test_resume_refuses_changed_preserved_state(field, mutation, message) -> None:
    before = snapshot()
    after_raw = inspect_fixture(secret="re_test_secret_value_long_enough")
    after_raw["Id"] = "d" * 64
    after = migration.snapshot_from_inspect(after_raw)
    mutation(after["definition"])
    with pytest.raises(migration.Refused, match=message):
        migration.validate_recovery_state(before, after)


def test_resume_refuses_missing_secret() -> None:
    before = snapshot()
    after_raw = inspect_fixture()
    after_raw["Id"] = "d" * 64
    after = migration.snapshot_from_inspect(after_raw)
    with pytest.raises(migration.Refused, match="presence"):
        migration.validate_recovery_state(before, after)


def test_resume_refuses_secret_source_mismatch() -> None:
    class SecretRunner:
        def run(self, _args):
            return migration.sha256_text("different-protected-secret")

    with pytest.raises(migration.Refused, match="does not match the protected source"):
        migration.secret_matches_container(
            SecretRunner(),
            "mas-stalwart-1",
            "operator-approved-protected-secret",
        )


def test_second_apply_refuses_to_recreate_again() -> None:
    before = snapshot()
    after_raw = inspect_fixture(secret="re_test_secret_value_long_enough")
    after_raw["Id"] = "d" * 64
    after = migration.snapshot_from_inspect(after_raw)
    with pytest.raises(migration.Refused, match="already recreated"):
        migration.validate_apply_start(before, after)


def test_rollback_preserves_definition_and_removes_secret() -> None:
    original = snapshot()
    restored_raw = inspect_fixture()
    restored_raw["Id"] = "f" * 64
    restored = migration.snapshot_from_inspect(restored_raw)
    migration.compare_preserved(original, restored, expect_secret=False)
    assert restored["resend_secret_present"] is False


def resolution_args(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        container="mas-stalwart-1",
        service="stalwart",
        project_name="mas",
        project_directory=tmp_path,
        compose_file=[tmp_path / "docker-compose.stalwart-canonical.yml"],
        compose_env_file=[],
        compose_profile=["mail-local"],
        override_file=tmp_path / "docker-compose.stalwart-resend-secret.yml",
        secret_file=tmp_path / "stalwart-resend.env",
        render_environment={"PATH": "/usr/bin"},
    )


class ResolutionRunner:
    def __init__(self, *, container_id: str, config_hash: str):
        self.container_id = container_id
        self.config_hash = config_hash

    def run(self, args, **_kwargs):
        if "ps" in args:
            return self.container_id
        if "config" in args and "--hash" in args:
            return f"stalwart {self.config_hash}"
        raise AssertionError(args)


def test_undefined_identity_service_dependency_is_categorized_and_sanitized(tmp_path: Path) -> None:
    stderr = (
        'service "tool-service" depends on undefined service "identity-service": '
        "invalid compose project PASSWORD=do-not-leak"
    )
    args = resolution_args(tmp_path)
    assert migration.compose_error_category(stderr) == "undefined_service_dependency"
    sanitized = migration.sanitize_compose_stderr(stderr, args)
    assert "identity-service" in sanitized
    assert "do-not-leak" not in sanitized
    assert "PASSWORD=<redacted>" in sanitized


def test_invalid_partial_compose_project_is_categorized() -> None:
    stderr = "service graph is incomplete: invalid compose project"
    assert migration.compose_error_category(stderr) == "invalid_partial_compose_project"


def test_valid_exact_stalwart_compose_resolution(tmp_path: Path) -> None:
    value = snapshot()
    runner = ResolutionRunner(
        container_id=value["container_id"],
        config_hash=value["compose"]["config_hash"],
    )
    assert (
        migration.require_compose_identity(
            runner,
            resolution_args(tmp_path),
            value,
            include_override=False,
        )
        == value["compose"]["config_hash"]
    )
    canonical = (
        ROOT
        / "mas"
        / "infra"
        / "smtp-gateway"
        / "home"
        / "docker-compose.stalwart-canonical.yml"
    ).read_text(encoding="utf-8")
    assert "stalwart:" in canonical
    assert "identity-service:" not in canonical
    assert "tool-service:" not in canonical


def test_running_container_id_mismatch_is_rejected(tmp_path: Path) -> None:
    value = snapshot()
    runner = ResolutionRunner(
        container_id="f" * 64,
        config_hash=value["compose"]["config_hash"],
    )
    with pytest.raises(migration.Refused, match="inspected Stalwart container"):
        migration.require_compose_identity(
            runner,
            resolution_args(tmp_path),
            value,
            include_override=False,
        )


def test_running_config_hash_mismatch_is_rejected(tmp_path: Path) -> None:
    value = snapshot()
    runner = ResolutionRunner(
        container_id=value["container_id"],
        config_hash="f" * 64,
    )
    with pytest.raises(migration.Refused, match="selected Compose definition"):
        migration.require_compose_identity(
            runner,
            resolution_args(tmp_path),
            value,
            include_override=False,
        )


def adopted_args(tmp_path: Path) -> SimpleNamespace:
    compose = tmp_path / "docker-compose.stalwart-canonical.yml"
    target = tmp_path / migration.ADOPTED_TARGET_COMPOSE_NAME
    override = tmp_path / migration.ADOPTED_SECRET_OVERRIDE_NAME
    for path in (compose, target, override):
        path.write_text("services:\n  stalwart:\n    image: pinned\n", encoding="utf-8")
    return SimpleNamespace(
        container="mas-stalwart-1",
        service="stalwart",
        project_name="mas",
        project_directory=tmp_path,
        compose_file=[compose, target],
        compose_env_file=[],
        compose_profile=["mail-local"],
        override_file=override,
        secret_file=tmp_path / "stalwart-resend.env",
        verification_secret_file=tmp_path / "resend-certification.env",
        backup_dir=tmp_path / "new-adoption",
        persistent_target="/var/lib/stalwart",
        account_id="w",
        approve_adopt_existing_secret=True,
        render_environment={"PATH": "/usr/bin"},
    )


def adopted_raw(args: SimpleNamespace, *, secret: str) -> dict:
    raw = inspect_fixture(secret=secret)
    raw["Image"] = migration.ADOPTED_TARGET_IMAGE_ID
    raw["Config"]["Image"] = migration.ADOPTED_TARGET_IMAGE
    raw["Config"]["Labels"].pop("aiat.role", None)
    raw["Config"]["Labels"]["com.docker.compose.project.working_dir"] = str(
        args.project_directory
    )
    raw["Config"]["Labels"]["com.docker.compose.project.config_files"] = ",".join(
        [str(path) for path in [*args.compose_file, args.override_file]]
    )
    return raw


class AdoptionRunner:
    def __init__(self, raw: dict, config_hash: str):
        self.raw = raw
        self.config_hash = config_hash
        self.commands: list[list[str]] = []

    def json(self, args):
        if args[:3] == ["docker", "inspect", "mas-stalwart-1"]:
            return [self.raw]
        if args[:3] == ["docker", "volume", "inspect"]:
            return [{"Labels": {"com.docker.compose.project": "mas"}}]
        raise AssertionError(args)

    def run(self, args, **_kwargs):
        self.commands.append(args)
        if "ps" in args:
            return self.raw["Id"]
        if "config" in args and "--hash" in args:
            return f"stalwart {self.config_hash}"
        if "exec" in args:
            return migration.sha256_text("adoption-secret")
        raise AssertionError(args)


def prepare_adoption_test(monkeypatch, tmp_path: Path):
    args = adopted_args(tmp_path)
    raw = adopted_raw(args, secret="adoption-secret")
    runner = AdoptionRunner(raw, raw["Config"]["Labels"]["com.docker.compose.config-hash"])
    monkeypatch.setattr(migration, "protected_secret", lambda _path: "adoption-secret")
    monkeypatch.setattr(migration, "validate_adopted_compose_semantics", lambda *_args: None)
    monkeypatch.setattr(
        migration,
        "certification_secrets",
        lambda _path: {
            "STALWART_API_KEY": "management",
            "STALWART_JMAP_SERVICE_TOKEN": "mail",
        },
    )
    monkeypatch.setattr(migration, "validate_certification_credentials", lambda *_args: None)
    monkeypatch.setattr(migration, "verify_stalwart_data", lambda *_args: None)
    monkeypatch.setattr(migration, "smtp_reachable", lambda *_args: None)
    return args, runner


def test_adopt_existing_certifies_without_compose_recreation_or_old_evidence_mutation(
    monkeypatch, tmp_path: Path
) -> None:
    args, runner = prepare_adoption_test(monkeypatch, tmp_path)
    old_evidence = tmp_path / "old-v0.16.7-evidence"
    old_evidence.mkdir()
    old_marker = old_evidence / "pre-migration-manifest.json"
    old_marker.write_text("immutable", encoding="utf-8")

    migration.adopt_existing_action(runner, args)

    assert not any("up" in command for command in runner.commands)
    assert old_marker.read_text(encoding="utf-8") == "immutable"
    assert (args.backup_dir / migration.ADOPTED_BASELINE_ARTIFACT).exists()
    assert (args.backup_dir / migration.ADOPTED_SUCCESS_ARTIFACT).exists()
    serialized = " ".join(
        path.read_text(encoding="utf-8")
        for path in args.backup_dir.iterdir()
    )
    assert "adoption-secret" not in serialized
    assert migration.sha256_text("adoption-secret") not in serialized


def test_adopt_existing_refuses_image_mismatch(monkeypatch, tmp_path: Path) -> None:
    args, _runner = prepare_adoption_test(monkeypatch, tmp_path)
    value = migration.snapshot_from_inspect(adopted_raw(args, secret="adoption-secret"))
    value["definition"]["image_id"] = "sha256:" + "f" * 64
    with pytest.raises(migration.Refused, match="image ID"):
        migration.validate_adopted_runtime(value)


def test_adopt_existing_refuses_untracked_mount(monkeypatch, tmp_path: Path) -> None:
    args, _runner = prepare_adoption_test(monkeypatch, tmp_path)
    value = migration.snapshot_from_inspect(adopted_raw(args, secret="adoption-secret"))
    class UntrackedRunner:
        def json(self, _args):
            return [{"Labels": {}}]

    with pytest.raises(migration.Refused, match="not tracked"):
        migration.validate_mount_tracking(UntrackedRunner(), value, project="mas")


def test_adopt_existing_refuses_stale_compose_hash(monkeypatch, tmp_path: Path) -> None:
    args, runner = prepare_adoption_test(monkeypatch, tmp_path)
    runner.config_hash = "f" * 64
    with pytest.raises(migration.Refused, match="selected Compose definition"):
        migration.adopt_existing_action(runner, args)


def test_adopt_existing_refuses_reused_backup_directory(monkeypatch, tmp_path: Path) -> None:
    args, runner = prepare_adoption_test(monkeypatch, tmp_path)
    args.backup_dir.mkdir()
    with pytest.raises(migration.Refused, match="new, non-existing"):
        migration.adopt_existing_action(runner, args)


def test_adopt_existing_refuses_invalid_credentials_without_artifacts(
    monkeypatch, tmp_path: Path
) -> None:
    args, runner = prepare_adoption_test(monkeypatch, tmp_path)
    monkeypatch.setattr(
        migration,
        "validate_certification_credentials",
        lambda *_args: (_ for _ in ()).throw(
            migration.Refused("certification credential validation failed")
        ),
    )
    with pytest.raises(migration.Refused, match="certification credential validation failed"):
        migration.adopt_existing_action(runner, args)
    assert not args.backup_dir.exists()


def test_verify_succeeds_against_adopted_baseline_without_recreation(monkeypatch, tmp_path: Path) -> None:
    args, runner = prepare_adoption_test(monkeypatch, tmp_path)
    migration.adopt_existing_action(runner, args)
    runner.commands.clear()

    migration.verify_action(runner, args)

    assert not any("up" in command for command in runner.commands)


def test_verify_stalwart_data_posts_only_to_discovered_jmap_endpoint(monkeypatch) -> None:
    calls: list[str] = []

    def fake_session(_url, _authorization):
        return {"apiUrl": "http://localhost:18080/jmap/"}

    def fake_post(url, _authorization, payload):
        calls.append(url)
        method = payload["methodCalls"][0][0]
        if method == "x:Domain/query":
            return {"methodResponses": [[method, {"ids": ["domain-id"]}, "domain"]]}
        if method == "x:Account/query":
            return {"methodResponses": [[method, {"ids": ["w"]}, "account"]]}
        return {"methodResponses": [["Mailbox/get", {"list": [{"id": "mailbox"}]}, "mailboxes"]]}

    monkeypatch.setattr(migration, "get_json", fake_session)
    monkeypatch.setattr(migration, "post_json", fake_post)
    migration.verify_stalwart_data(
        SimpleNamespace(account_id="w"),
        {
            "STALWART_API_KEY": "management",
            "STALWART_JMAP_SERVICE_TOKEN": "Bearer mailbox",
        },
    )
    assert calls
    assert all(url == "http://127.0.0.1:18080/jmap/" for url in calls)
    assert all(not url.endswith("/api") for url in calls)
