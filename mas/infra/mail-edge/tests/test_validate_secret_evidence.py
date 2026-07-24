from __future__ import annotations

import subprocess
import sys
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "validate-secret-evidence.py"
)


def _run(tmp_path: Path, env_text: str, evidence_text: str) -> subprocess.CompletedProcess[str]:
    env_file = tmp_path / "mail-edge.env"
    evidence_file = tmp_path / "evidence.log"
    env_file.write_text(env_text, encoding="utf-8")
    evidence_file.write_text(evidence_text, encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(env_file), str(evidence_file)],
        check=False,
        capture_output=True,
        text=True,
    )


def test_secret_evidence_validator_accepts_clean_evidence(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        "RESEND_API_KEY=provider-secret-fixture\n",
        "relay configured; secret value omitted\n",
    )

    assert result.returncode == 0
    assert "no configured secret values found" in result.stdout
    assert "provider-secret-fixture" not in result.stdout + result.stderr


def test_secret_evidence_validator_reports_only_the_variable_name(
    tmp_path: Path,
) -> None:
    result = _run(
        tmp_path,
        "RESEND_API_KEY=provider-secret-fixture\n",
        "unsafe evidence provider-secret-fixture\n",
    )

    assert result.returncode == 1
    assert "secret value detected for RESEND_API_KEY" in result.stderr
    assert "provider-secret-fixture" not in result.stdout + result.stderr


def test_secret_evidence_validator_fails_closed_without_configured_secrets(
    tmp_path: Path,
) -> None:
    result = _run(tmp_path, "RESEND_API_KEY=\n", "clean evidence\n")

    assert result.returncode == 2
    assert "no configured mail-edge secrets" in result.stderr
