from __future__ import annotations

import importlib.util
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
