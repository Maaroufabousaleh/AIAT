"""Operational regression tests for the mail-edge backup wrapper."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "run-backup.sh"


@pytest.mark.parametrize(
    ("stalwart_running", "identity_running", "expected_starts"),
    [
        (True, True, {"stalwart", "identity-service"}),
        (True, False, {"stalwart"}),
        (False, True, {"identity-service"}),
        (False, False, set()),
    ],
)
def test_backup_restores_only_the_services_that_were_running(
    tmp_path: Path,
    stalwart_running: bool,
    identity_running: bool,
    expected_starts: set[str],
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "docker-calls"
    docker = bin_dir / "docker"
    docker.write_text(
        """#!/bin/sh
set -eu
printf '%s\\n' "$*" >> "$FAKE_DOCKER_CALLS"
case "$*" in
  *"ps --status running -q stalwart"*)
    test "$FAKE_STALWART_RUNNING" = true && printf '%s\\n' stalwart-container
    ;;
  *"ps --status running -q identity-service"*)
    test "$FAKE_IDENTITY_RUNNING" = true && printf '%s\\n' identity-container
    ;;
esac
""",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    env_file = tmp_path / "mail-edge.env"
    env_file.write_text("PLACEHOLDER_ONLY=true\n", encoding="utf-8")
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "FAKE_DOCKER_CALLS": str(calls),
        "FAKE_STALWART_RUNNING": str(stalwart_running).lower(),
        "FAKE_IDENTITY_RUNNING": str(identity_running).lower(),
    }

    subprocess.run(
        [str(SCRIPT), str(env_file)],
        check=True,
        env=env,
        text=True,
        capture_output=True,
    )

    invocations = calls.read_text(encoding="utf-8").splitlines()
    assert any("stop identity-service stalwart" in call for call in invocations)
    assert any("--profile backup run --rm encrypted-backup" in call for call in invocations)
    actual_starts = {
        service
        for service in ("stalwart", "identity-service")
        if any(call.endswith(f" start {service}") for call in invocations)
    }
    assert actual_starts == expected_starts
