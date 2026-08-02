from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

GATEWAY = Path(__file__).resolve().parents[1]
SCRIPT = GATEWAY / "scripts" / "finish_resend_route_activation.py"
SPEC = importlib.util.spec_from_file_location("finish_resend_route_activation", SCRIPT)
assert SPEC and SPEC.loader
finish = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(finish)


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


def test_admin_source_uses_only_password_component_and_required_keys(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(finish, "_require_root_file", lambda path, **_kwargs: path.stat())
    source = tmp_path / ".env"
    source.write_text(
        "OTHER=value\nSTALWART_RECOVERY_ADMIN=admin:password-component\n"
        "admin-st=temporary-source\nguest=guest-source\n",
        encoding="utf-8",
    )
    assert finish.read_permanent_admin_password(source) == "password-component"


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
