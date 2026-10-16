"""Process cleanup and boundary claims for the source-bound scanner."""

from __future__ import annotations

import asyncio
import json
import os
import pwd
import shutil
import sys
import tempfile
import time
from pathlib import Path

import pytest
from orchestrator_api import worker_security_scan

pytestmark = pytest.mark.skipif(os.name != "posix", reason="scanner process groups require POSIX")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _scanner_temp_dir() -> Path:
    path = Path(tempfile.mkdtemp(prefix="aiat-scan-cleanup-test-", dir="/tmp"))
    path.chmod(0o700)
    if os.geteuid() == 0:
        nobody = pwd.getpwnam("nobody")
        os.chown(path, nobody.pw_uid, nobody.pw_gid)
    return path


def _process_running(pid: int) -> bool:
    stat_path = Path(f"/proc/{pid}/stat")
    try:
        status = stat_path.read_text(encoding="ascii")
    except FileNotFoundError:
        return False
    state = status.rsplit(")", maxsplit=1)[-1].strip().split(maxsplit=1)[0]
    return state != "Z"


async def _wait_for_processes_to_stop(pids: list[int]) -> None:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if not any(_process_running(pid) for pid in pids):
            return
        await asyncio.sleep(0.02)
    assert not any(_process_running(pid) for pid in pids)


def _spawn_script(pid_file: Path, *, exit_leader: bool) -> str:
    child_code = "import time; time.sleep(60)"
    leader_action = "raise SystemExit(0)" if exit_leader else "time.sleep(60)"
    return (
        "import json, subprocess, sys, time; "
        f"child = subprocess.Popen([{sys.executable!r}, '-c', {child_code!r}]); "
        f"open({str(pid_file)!r}, 'w', encoding='ascii').write(json.dumps("
        "{'leader': __import__('os').getpid(), 'child': child.pid})); "
        f"{leader_action}"
    )


def _scanner_env(path: Path) -> dict[str, str]:
    return {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(path), "TMPDIR": str(path)}


@pytest.mark.anyio
async def test_scanner_timeout_kills_descendant_after_leader_exits(monkeypatch) -> None:
    temp_dir = _scanner_temp_dir()
    pid_file = temp_dir / "pids.json"
    monkeypatch.setattr(worker_security_scan, "MAX_SCANNER_TIMEOUT_SECONDS", 0.25)
    try:
        result = await worker_security_scan._execute_bounded(
            [sys.executable, "-c", _spawn_script(pid_file, exit_leader=True)],
            cwd=temp_dir,
            env=_scanner_env(temp_dir),
            stdout_limit=4096,
            stderr_limit=4096,
        )

        assert result["error"] == "scanner_timeout"
        pids = json.loads(pid_file.read_text(encoding="ascii"))
        await _wait_for_processes_to_stop([pids["leader"], pids["child"]])
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.mark.anyio
async def test_scanner_task_cancellation_kills_leader_and_descendant() -> None:
    temp_dir = _scanner_temp_dir()
    pid_file = temp_dir / "pids.json"
    try:
        task = asyncio.create_task(
            worker_security_scan._execute_bounded(
                [sys.executable, "-c", _spawn_script(pid_file, exit_leader=False)],
                cwd=temp_dir,
                env=_scanner_env(temp_dir),
                stdout_limit=4096,
                stderr_limit=4096,
            )
        )
        deadline = time.monotonic() + 3
        while not pid_file.exists() and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        assert pid_file.exists()
        pids = json.loads(pid_file.read_text(encoding="ascii"))

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        await _wait_for_processes_to_stop([pids["leader"], pids["child"]])
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
