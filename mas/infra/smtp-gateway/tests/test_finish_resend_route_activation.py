from __future__ import annotations

import importlib.util
import json
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

GATEWAY = Path(__file__).resolve().parents[1]
SCRIPT = GATEWAY / "scripts" / "finish_resend_route_activation.py"
SPEC = importlib.util.spec_from_file_location("finish_resend_route_activation", SCRIPT)
assert SPEC and SPEC.loader
finish = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(finish)
admin_source = finish.ADMIN_SOURCE

VALID_ADMIN_SOURCE = (
    "STALWART_RECOVERY_ADMIN=admin:password-component\n"
    "admin-st=temporary-source\n"
    "guest=guest-source\n"
)


def _write_control(tmp_path: Path, **overrides: bool) -> Path:
    values = {key: key in finish.REQUIRED_TRUE_CONTROLS for key in finish.CONTROL_KEYS}
    values.update(overrides)
    path = tmp_path / "email-route-finish.env"
    path.write_text(
        "\n".join(f"{key}={'true' if values[key] else 'false'}" for key in finish.CONTROL_KEYS)
        + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)
    return path


def _prepare_initial_finish(tmp_path: Path, monkeypatch) -> tuple[Path, Path, Path]:
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    route_secret = tmp_path / "stalwart-route-lifecycle.env"
    route_metadata = tmp_path / "stalwart-route-lifecycle.meta"
    monkeypatch.setattr(finish, "LOCK_FILE", tmp_path / "finish.lock")
    monkeypatch.setattr(finish, "ROUTE_SECRET_FILE", route_secret)
    monkeypatch.setattr(finish, "ROUTE_METADATA_FILE", route_metadata)
    monkeypatch.setattr(
        finish,
        "parse_control_file",
        lambda _path: {
            key: key in finish.REQUIRED_TRUE_CONTROLS for key in finish.CONTROL_KEYS
        },
    )
    monkeypatch.setattr(finish, "_create_evidence_dir", lambda: evidence_dir)
    monkeypatch.setattr(
        finish,
        "_parse_profile",
        lambda _path: {
            "OUTBOUND_RELAY_CERTIFIED": "false",
            "DIRECT_MX_OUTBOUND_ENABLED": "false",
            "DEFAULT_OUTBOUND_ENABLED": "false",
        },
    )
    monkeypatch.setattr(
        finish,
        "read_permanent_admin_password",
        lambda _path: "test-admin-password",
    )
    monkeypatch.setattr(
        finish,
        "_read_certification_values",
        lambda _path: {
            "STALWART_API_KEY": "test-certification-key",
            "STALWART_JMAP_SERVICE_TOKEN": "test-service-token",
        },
    )
    monkeypatch.setattr(finish, "_read_relay_secret", lambda _path: "test-relay-secret")
    monkeypatch.setattr(
        finish,
        "_require_root_file",
        lambda _path, **_kwargs: SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_uid=0),
    )
    return evidence_dir, route_secret, route_metadata


def test_control_file_requires_safe_approvals(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(finish, "_require_root_file", lambda path, **_kwargs: path.stat())
    control = _write_control(tmp_path)
    assert finish.parse_control_file(control)["STOP_AFTER_CERTIFICATION_PREFLIGHT"] is True

    unsafe = _write_control(tmp_path, APPROVE_CERTIFICATION_MESSAGE=True)
    with pytest.raises(finish.FinishRefused, match="must remain false"):
        finish.parse_control_file(unsafe)


def test_control_file_rejects_unknown_duplicate_and_malformed_lines(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(finish, "_require_root_file", lambda path, **_kwargs: path.stat())
    control = _write_control(tmp_path)
    original = control.read_text(encoding="utf-8")
    control.write_text(original + "UNKNOWN=true\n", encoding="utf-8")
    with pytest.raises(finish.FinishRefused):
        finish.parse_control_file(control)
    control.write_text(
        original.replace("STOP_AFTER_CERTIFICATION_PREFLIGHT=true\n", ""), encoding="utf-8"
    )
    with pytest.raises(finish.FinishRefused):
        finish.parse_control_file(control)
    control.write_text(
        original.replace(
            "STOP_AFTER_CERTIFICATION_PREFLIGHT=true", "STOP_AFTER_CERTIFICATION_PREFLIGHT=yes"
        ),
        encoding="utf-8",
    )
    with pytest.raises(finish.FinishRefused):
        finish.parse_control_file(control)


def test_default_and_override_admin_source_are_threaded_to_finish(
    monkeypatch, capsys
) -> None:
    captured: list[tuple[Path, Path]] = []

    def fake_run_finish(control_file: Path, admin_source_file: Path):
        captured.append((control_file, admin_source_file))
        return (
            {
                "starting_commit": "a" * 40,
                "final_commit": "b" * 40,
                "evidence_dir": "/secure/rollback/evidence",
            },
            Path("/secure/rollback/evidence"),
        )

    monkeypatch.setattr(finish.os, "geteuid", lambda: 0)
    monkeypatch.setattr(finish, "_run_finish", fake_run_finish)
    assert Path("/etc/aiat/stalwart-admin-source.env") == finish.ADMIN_SOURCE_FILE

    assert finish.main(["--control-file", "/tmp/control.env"]) == 0
    assert finish.main(
        [
            "--control-file",
            "/tmp/control.env",
            "--admin-source-file",
            "/tmp/admin-source.env",
        ]
    ) == 0
    assert captured == [
        (Path("/tmp/control.env"), finish.ADMIN_SOURCE_FILE),
        (Path("/tmp/control.env"), Path("/tmp/admin-source.env")),
    ]
    assert "FINAL_STATUS=BLOCKED" not in capsys.readouterr().out


def test_repository_source_does_not_require_protected_file_metadata(tmp_path: Path) -> None:
    source = tmp_path / ".env"
    source.write_text(
        "OPTIONAL_EMPTY=\n"
        "STALWART_RECOVERY_ADMIN=admin:password-component\n"
        "admin-st=temporary-source\n"
        "guest=guest-source\n"
        "OTHER=overridden\n"
        "OTHER=value\n",
        encoding="utf-8",
    )
    source.chmod(0o644)
    assert admin_source.parse_repository_source(source) == {
        "STALWART_RECOVERY_ADMIN": "admin:password-component",
        "admin-st": "temporary-source",
        "guest": "guest-source",
    }


def test_protected_admin_source_accepts_exact_three_key_shape() -> None:
    assert admin_source.validate_protected_admin_source_text(VALID_ADMIN_SOURCE) == {
        "STALWART_RECOVERY_ADMIN": "admin:password-component",
        "admin-st": "temporary-source",
        "guest": "guest-source",
    }


@pytest.mark.parametrize(
    "value",
    [
        "STALWART_RECOVERY_ADMIN=admin:password-component\nadmin-st=temporary-source\n",
        VALID_ADMIN_SOURCE + "extra=value\n",
        "STALWART_RECOVERY_ADMIN=admin:password-component\n"
        "STALWART_RECOVERY_ADMIN=admin:second-password\n"
        "admin-st=temporary-source\n",
        "STALWART_RECOVERY_ADMIN=admin:password-component\n"
        "admin-st=temporary-source\n"
        "malformed\n"
        "guest=guest-source\n",
        "STALWART_RECOVERY_ADMIN=admin:password-component\n"
        "admin-st=temporary-source\n"
        "guest=\n",
    ],
    ids=["missing-key", "extra-key", "duplicate-key", "malformed-line", "empty-value"],
)
def test_protected_admin_source_rejects_invalid_shapes(value: str) -> None:
    with pytest.raises(admin_source.AdminSourceRefused):
        admin_source.validate_protected_admin_source_text(value)


def test_protected_admin_source_rejects_missing_newline() -> None:
    with pytest.raises(admin_source.AdminSourceRefused):
        admin_source.validate_protected_admin_source_text(VALID_ADMIN_SOURCE.rstrip("\n"))


def test_protected_admin_source_refuses_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.write_text(VALID_ADMIN_SOURCE, encoding="utf-8")
    source = tmp_path / "source"
    source.symlink_to(target)
    with pytest.raises(admin_source.AdminSourceRefused):
        admin_source.read_protected_admin_source(source)


def test_protected_admin_source_refuses_incorrect_owner_and_mode() -> None:
    regular = stat.S_IFREG
    with pytest.raises(admin_source.AdminSourceRefused):
        admin_source._check_regular(
            SimpleNamespace(st_mode=regular | 0o600, st_uid=1000, st_gid=0),
            protected=True,
        )
    with pytest.raises(admin_source.AdminSourceRefused):
        admin_source._check_regular(
            SimpleNamespace(st_mode=regular | 0o644, st_uid=0, st_gid=0),
            protected=True,
        )


def test_admin_source_install_is_atomic_and_does_not_modify_repository(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / ".env"
    source_text = (
        "OTHER=unchanged\n"
        "STALWART_RECOVERY_ADMIN=admin:password-component\n"
        "admin-st=temporary-source\n"
        "guest=guest-source\n"
    )
    source.write_text(source_text, encoding="utf-8")
    destination = tmp_path / "stalwart-admin-source.env"
    destination.write_text("old=value\n", encoding="utf-8")
    replaced: list[tuple[Path, Path]] = []
    real_replace = admin_source.os.replace

    def record_replace(temporary: str | bytes, target: str | bytes) -> None:
        temporary_path = Path(temporary)
        target_path = Path(target)
        assert temporary_path.parent == target_path.parent
        assert temporary_path.read_text(encoding="utf-8") == VALID_ADMIN_SOURCE
        replaced.append((temporary_path, target_path))
        real_replace(temporary, target)

    monkeypatch.setattr(admin_source.os, "geteuid", lambda: 0)
    monkeypatch.setattr(admin_source, "_ensure_destination_parent", lambda _path: None)
    monkeypatch.setattr(admin_source.os, "fchown", lambda _fd, _uid, _gid: None)
    monkeypatch.setattr(
        admin_source,
        "read_protected_admin_source",
        lambda path: admin_source.validate_protected_admin_source_text(
            Path(path).read_text(encoding="utf-8")
        ),
    )
    monkeypatch.setattr(admin_source.os, "replace", record_replace)

    admin_source.install_admin_source(source, destination)
    assert len(replaced) == 1
    assert replaced[0][1] == destination
    assert destination.read_text(encoding="utf-8") == VALID_ADMIN_SOURCE
    assert source.read_text(encoding="utf-8") == source_text
    assert not list(tmp_path.glob(f".{destination.name}.*"))


def test_installer_stdout_and_stderr_never_contain_source_values(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    secret = "SUPER_SECRET_VALUE"
    source = tmp_path / ".env"
    source.write_text(f"STALWART_RECOVERY_ADMIN=admin:{secret}\n", encoding="utf-8")
    destination = tmp_path / "destination"
    monkeypatch.setattr(admin_source.os, "geteuid", lambda: 0)
    assert (
        admin_source.main(
            ["--source-file", str(source), "--destination-file", str(destination)]
        )
        == 1
    )
    captured = capsys.readouterr()
    assert secret not in captured.out
    assert secret not in captured.err
    assert "FINAL_STATUS=FAIL" in captured.out
    assert str(destination) in captured.out


def test_activation_rejects_invalid_source_without_echoing_values(tmp_path: Path) -> None:
    secret = "SUPER_SECRET_VALUE"
    source = tmp_path / "admin-source.env"
    source.write_text(
        f"STALWART_RECOVERY_ADMIN=admin:{secret}\nadmin-st=temporary-source\n",
        encoding="utf-8",
    )
    with pytest.raises(finish.FinishRefused, match="protected admin source is invalid") as error:
        finish.read_permanent_admin_password(source)
    assert secret not in str(error.value)


def test_permanent_admin_password_uses_admin_st_not_recovery_pair(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        admin_source,
        "read_protected_admin_source",
        lambda _path: {
            "STALWART_RECOVERY_ADMIN": "admin:recovery-only-password",
            "admin-st": "permanent-admin-password",
            "guest": "app_gateway-password",
        },
    )
    assert (
        admin_source.read_permanent_admin_password(Path("/protected/admin-source.env"))
        == "permanent-admin-password"
    )


def test_run_finish_reads_the_selected_dedicated_source(tmp_path: Path, monkeypatch) -> None:
    selected = tmp_path / "dedicated-admin-source.env"
    observed: list[Path] = []

    def fail_after_source(_path: Path) -> dict[str, str]:
        raise finish.FinishRefused("stop before route work")

    monkeypatch.setattr(finish, "parse_control_file", lambda _path: {})
    monkeypatch.setattr(finish, "LOCK_FILE", tmp_path / "finish.lock")
    monkeypatch.setattr(finish, "_create_evidence_dir", lambda: tmp_path)
    monkeypatch.setattr(finish, "_parse_profile", lambda _path: {})
    monkeypatch.setattr(
        finish,
        "read_permanent_admin_password",
        lambda path: observed.append(path) or "password-component",
    )
    monkeypatch.setattr(finish, "_read_certification_values", fail_after_source)

    with pytest.raises(finish.FinishRefused, match="stop before route work"):
        finish._run_finish(tmp_path / "control.env", selected)
    assert observed == [selected]


def test_assert_absent_accepts_both_missing_temporary_artifacts(tmp_path: Path) -> None:
    finish._assert_absent(
        tmp_path / "stalwart-route-lifecycle.env",
        tmp_path / "stalwart-route-lifecycle.meta",
    )


def test_assert_absent_refuses_regular_secret_file(tmp_path: Path) -> None:
    artifact = tmp_path / "stalwart-route-lifecycle.env"
    artifact.write_text("must-remain-unchanged\n", encoding="utf-8")
    with pytest.raises(finish.FinishRefused, match=artifact.name):
        finish._assert_absent(artifact)
    assert artifact.read_text(encoding="utf-8") == "must-remain-unchanged\n"


def test_assert_absent_refuses_metadata_file(tmp_path: Path) -> None:
    artifact = tmp_path / "stalwart-route-lifecycle.meta"
    artifact.write_text("metadata-must-remain\n", encoding="utf-8")
    with pytest.raises(finish.FinishRefused, match=artifact.name):
        finish._assert_absent(artifact)
    assert artifact.read_text(encoding="utf-8") == "metadata-must-remain\n"


def test_assert_absent_refuses_valid_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.write_text("target-remains\n", encoding="utf-8")
    artifact = tmp_path / "stalwart-route-lifecycle.env"
    artifact.symlink_to(target)
    with pytest.raises(finish.FinishRefused, match=artifact.name):
        finish._assert_absent(artifact)
    assert artifact.is_symlink()
    assert target.read_text(encoding="utf-8") == "target-remains\n"


def test_assert_absent_refuses_broken_symlink(tmp_path: Path) -> None:
    artifact = tmp_path / "stalwart-route-lifecycle.env"
    artifact.symlink_to(tmp_path / "missing-target")
    with pytest.raises(finish.FinishRefused, match=artifact.name):
        finish._assert_absent(artifact)
    assert artifact.is_symlink()


def test_assert_absent_refuses_directory(tmp_path: Path) -> None:
    artifact = tmp_path / "stalwart-route-lifecycle.env"
    artifact.mkdir()
    with pytest.raises(finish.FinishRefused, match=artifact.name):
        finish._assert_absent(artifact)
    assert artifact.is_dir()


def test_assert_absent_fails_closed_on_lstat_error(tmp_path: Path, monkeypatch) -> None:
    artifact = tmp_path / "stalwart-route-lifecycle.env"

    def refuse_lstat(_path: Path):
        raise PermissionError("authorization=SUPER_SECRET")

    monkeypatch.setattr(Path, "lstat", refuse_lstat)
    with pytest.raises(finish.FinishRefused) as error:
        finish._assert_absent(artifact)
    message = str(error.value)
    assert message == f"could not inspect temporary artifact {artifact.name}"
    assert "SUPER_SECRET" not in message


def test_assert_absent_checks_later_paths(tmp_path: Path) -> None:
    first = tmp_path / "stalwart-route-lifecycle.env"
    later = tmp_path / "stalwart-route-lifecycle.meta"
    later.write_text("metadata-remains\n", encoding="utf-8")
    with pytest.raises(finish.FinishRefused, match=later.name):
        finish._assert_absent(first, later)
    assert later.read_text(encoding="utf-8") == "metadata-remains\n"


def test_assert_absent_sanitizes_untrusted_artifact_name(tmp_path: Path) -> None:
    artifact = tmp_path / "password=SUPER_SECRET_VALUE"
    artifact.write_text("contents-are-never-read\n", encoding="utf-8")
    with pytest.raises(finish.FinishRefused) as error:
        finish._assert_absent(artifact)
    message = str(error.value)
    assert "temporary-artifact" in message
    assert "SUPER_SECRET_VALUE" not in message
    assert str(tmp_path) not in message


def test_failed_absence_gate_performs_no_route_or_credential_mutation(
    tmp_path: Path, monkeypatch
) -> None:
    _evidence_dir, route_secret, _route_metadata = _prepare_initial_finish(
        tmp_path, monkeypatch
    )
    route_secret.write_text("existing-secret-remains\n", encoding="utf-8")
    calls: list[str] = []

    def forbidden(name: str):
        def invoke(*_args, **_kwargs):
            calls.append(name)
            raise AssertionError(f"unexpected mutation: {name}")

        return invoke

    for name in (
        "_provision_route_key",
        "_validate_route_key",
        "_run_route_command",
        "_run_certification_validator",
        "_run_certification_preflight",
        "_revoke_route_key",
        "_remove_create_permission",
    ):
        monkeypatch.setattr(finish, name, forbidden(name))

    with pytest.raises(finish.FinishRefused, match=route_secret.name):
        finish._run_finish(tmp_path / "control.env", tmp_path / "admin-source.env")
    assert calls == []
    assert route_secret.read_text(encoding="utf-8") == "existing-secret-remains\n"


def test_initial_governed_preflight_passes_the_absence_gate(
    tmp_path: Path, monkeypatch
) -> None:
    _prepare_initial_finish(tmp_path, monkeypatch)
    reached_after_absence_check: list[bool] = []

    def stop_after_absence_check() -> dict[str, object]:
        reached_after_absence_check.append(True)
        raise finish.FinishRefused("reached post-absence read-only snapshot")

    monkeypatch.setattr(finish, "_git_snapshot", stop_after_absence_check)
    with pytest.raises(finish.FinishRefused, match="post-absence"):
        finish._run_finish(tmp_path / "control.env", tmp_path / "admin-source.env")
    assert reached_after_absence_check == [True]


def test_unexpected_failure_evidence_contains_only_safe_traceback_metadata(
    tmp_path: Path, monkeypatch
) -> None:
    evidence_dir, _route_secret, _route_metadata = _prepare_initial_finish(
        tmp_path, monkeypatch
    )

    class SecretArtifactObject:
        name = "stalwart-route-lifecycle.env"

    monkeypatch.setattr(finish, "ROUTE_SECRET_FILE", SecretArtifactObject())
    with pytest.raises(AttributeError):
        finish._run_finish(tmp_path / "control.env", tmp_path / "admin-source.env")

    failure = json.loads((evidence_dir / "failure.json").read_text(encoding="utf-8"))
    assert failure == {
        "version": 1,
        "final_status": "BLOCKED",
        "reason": "unexpected AttributeError",
        "exception_type": "AttributeError",
        "source_filename": "finish_resend_route_activation.py",
        "line_number": failure["line_number"],
        "function_name": "_assert_absent",
    }
    assert isinstance(failure["line_number"], int) and failure["line_number"] > 0
    serialized = json.dumps(failure, sort_keys=True)
    for sensitive in (
        "SecretArtifactObject",
        "test-admin-password",
        "test-certification-key",
        "test-service-token",
        "test-relay-secret",
        "authorization",
    ):
        assert sensitive not in serialized


def test_git_snapshot_uses_exact_per_command_safe_directory(monkeypatch) -> None:
    calls: list[list[str]] = []
    outputs = iter(["a" * 40, ""])

    def capture(command, **_kwargs):
        calls.append(list(command))
        return next(outputs)

    monkeypatch.setattr(finish, "_capture", capture)
    assert finish._git_snapshot() == {"commit": "a" * 40, "worktree_dirty": False}
    safe_directory = f"safe.directory={finish.WORKSPACE}"
    assert calls == [
        ["git", "-c", safe_directory, "rev-parse", "HEAD"],
        [
            "git",
            "-c",
            safe_directory,
            "status",
            "--porcelain",
            "--untracked-files=no",
        ],
    ]
    assert all("config" not in command for command in calls)
    assert all("--global" not in command and "--system" not in command for command in calls)


def test_git_snapshot_source_never_mutates_global_or_system_config() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert "git config" not in source
    assert "--global" not in source
    assert "--system" not in source


def test_git_failure_is_fail_closed_and_preserves_sanitized_subprocess(
    monkeypatch,
) -> None:
    secret = "SUPER_SECRET_AUTHORIZATION_VALUE"
    observed: dict[str, object] = {}

    def fail_git(command, **kwargs):
        observed["command"] = list(command)
        observed.update(kwargs)
        return SimpleNamespace(
            returncode=128,
            stdout="",
            stderr=f"fatal: authorization={secret}",
        )

    monkeypatch.setattr(finish.subprocess, "run", fail_git)
    with pytest.raises(finish.FinishRefused, match="read-only local inspection failed") as error:
        finish._git_snapshot()
    assert secret not in str(error.value)
    assert observed["command"] == [
        "git",
        "-c",
        f"safe.directory={finish.WORKSPACE}",
        "rev-parse",
        "HEAD",
    ]
    assert observed["cwd"] == finish.WORKSPACE
    assert observed["env"] == finish._safe_environment()
    assert observed["stdin"] == finish.subprocess.DEVNULL
    assert observed["capture_output"] is True
    assert observed["text"] is True
    assert observed["timeout"] == 30
    assert observed["check"] is False


@pytest.mark.parametrize(
    "commit",
    [
        "a" * 39,
        "a" * 41,
        "A" * 40,
        "g" * 40,
        f"{'a' * 40}\n{'b' * 40}",
    ],
    ids=["short", "long", "uppercase", "non-hex", "multiple-lines"],
)
def test_git_snapshot_rejects_malformed_commit_output(commit: str, monkeypatch) -> None:
    calls: list[list[str]] = []

    def capture(command, **_kwargs):
        calls.append(list(command))
        return commit

    monkeypatch.setattr(finish, "_capture", capture)
    with pytest.raises(finish.FinishRefused, match="valid commit identifier"):
        finish._git_snapshot()
    assert len(calls) == 1
    assert calls[0][-2:] == ["rev-parse", "HEAD"]


@pytest.mark.parametrize(
    ("status", "expected_dirty"),
    [("", False), (" M tracked-file.py", True)],
    ids=["clean", "dirty"],
)
def test_git_snapshot_detects_clean_and_dirty_worktrees(
    status: str, expected_dirty: bool, monkeypatch
) -> None:
    outputs = iter(["b" * 40, status])
    monkeypatch.setattr(finish, "_capture", lambda _command, **_kwargs: next(outputs))
    assert finish._git_snapshot() == {
        "commit": "b" * 40,
        "worktree_dirty": expected_dirty,
    }


def test_docker_snapshot_command_remains_unchanged(monkeypatch) -> None:
    calls: list[list[str]] = []
    template = (
        "{{.Id}}\t{{.Config.Image}}\t{{.State.Running}}\t{{.State.OOMKilled}}\t"
        "{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}\t"
        "{{range .Mounts}}{{.Destination}}={{.RW}};{{end}}"
    )

    def capture(command, **_kwargs):
        calls.append(list(command))
        return f"container-id\t{finish.PINNED_IMAGE}\ttrue\tfalse\thealthy\t/data=true;"

    monkeypatch.setattr(finish, "_capture", capture)
    snapshot = finish._container_snapshot()
    assert snapshot["running"] == "true"
    assert calls == [
        ["docker", "inspect", "--format", template, finish.STALWART_CONTAINER]
    ]
    assert "safe.directory" not in " ".join(calls[0])


def test_git_snapshot_failure_blocks_mutation_and_writes_secret_safe_evidence(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    evidence_dir, _route_secret, _route_metadata = _prepare_initial_finish(
        tmp_path, monkeypatch
    )
    secret = "SUPER_SECRET_GIT_VALUE"
    calls: list[str] = []

    def fail_git_snapshot():
        raise finish.FinishRefused(f"Git snapshot failed password={secret}")

    def forbidden(name: str):
        def invoke(*_args, **_kwargs):
            calls.append(name)
            raise AssertionError(f"unexpected mutation: {name}")

        return invoke

    monkeypatch.setattr(finish, "_git_snapshot", fail_git_snapshot)
    for name in (
        "_provision_route_key",
        "_validate_route_key",
        "_run_route_command",
        "_run_certification_validator",
        "_run_certification_preflight",
        "_revoke_route_key",
        "_remove_create_permission",
    ):
        monkeypatch.setattr(finish, name, forbidden(name))

    with pytest.raises(finish.FinishRefused):
        finish._run_finish(tmp_path / "control.env", tmp_path / "admin-source.env")
    assert calls == []
    captured = capsys.readouterr()
    assert secret not in captured.out
    assert secret not in captured.err
    failure = (evidence_dir / "failure.json").read_text(encoding="utf-8")
    assert secret not in failure
    assert "password=<redacted>" in failure


def test_git_failure_operator_output_is_secret_safe(monkeypatch, capsys) -> None:
    secret = "SUPER_SECRET_GIT_VALUE"

    def fail_finish(_control_file: Path, _admin_source_file: Path):
        raise finish.FinishRefused(f"Git snapshot failed token={secret}")

    monkeypatch.setattr(finish.os, "geteuid", lambda: 0)
    monkeypatch.setattr(finish, "_run_finish", fail_finish)
    assert finish.main(["--control-file", "/tmp/control.env"]) == 1
    captured = capsys.readouterr()
    assert secret not in captured.out
    assert secret not in captured.err
    assert "token=<redacted>" in captured.out


def test_route_commands_keep_apply_key_separate_from_read_only_certificate_key(
    monkeypatch,
) -> None:
    calls: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(
        finish, "_run", lambda label, command, **_kwargs: calls.append((label, command))
    )
    route_key = Path("/etc/aiat/stalwart-route-lifecycle.env")
    cert_key = Path("/etc/aiat/resend-certification.env")
    relay = Path("/etc/aiat/stalwart-resend.env")
    finish._run_route_command("apply", route_key, relay)
    finish._run_route_command("verify", cert_key, relay)
    apply_label, apply_command = calls[0]
    verify_label, verify_command = calls[1]
    assert apply_label == "route apply"
    assert verify_label == "route verify"
    assert str(route_key) in apply_command
    assert str(finish.ROUTE_METADATA_FILE) in apply_command
    assert str(cert_key) in verify_command
    assert str(finish.ROUTE_METADATA_FILE) not in verify_command


def test_route_inspect_http_401_has_specific_secret_safe_refusal(
    monkeypatch, capsys
) -> None:
    secret = "API_DO_NOT_LEAK"
    monkeypatch.setattr(
        finish.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1,
            stdout="",
            stderr=(
                "Stalwart JMAP endpoint discovery refused: "
                f"JMAP session request failed: HTTP_STATUS=401 token={secret}"
            ),
        ),
    )
    with pytest.raises(
        finish.CertificationAuthenticationRefused,
        match="certification API key authentication failed during route inspection",
    ) as caught:
        finish._run("route inspect", ["sh", "route-inspect"])
    assert caught.value.safe_context == {
        "operation": "route-inspect",
        "endpoint_path": "/jmap/session",
        "http_status": 401,
        "authentication_mechanism": "bearer-certification-api-key",
        "exception_class": "JmapEndpointError",
    }
    captured = capsys.readouterr()
    assert secret not in captured.out
    assert secret not in captured.err


def test_orchestrator_source_has_no_certification_mutation() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert '"Email/set"' not in source
    assert '"EmailSubmission/set"' not in source
    assert "EmailSubmission/query" in source
    assert "CERTIFICATION_SCRIPT" not in source


def test_non_root_invocation_refuses_before_reading_controls(monkeypatch, capsys) -> None:
    monkeypatch.setattr(finish.os, "geteuid", lambda: 1000)
    assert finish.main(["--control-file", "/not/read"]) == 1
    output = capsys.readouterr().err
    assert "FINAL_STATUS=BLOCKED" in output
    assert "/not/read" not in output
