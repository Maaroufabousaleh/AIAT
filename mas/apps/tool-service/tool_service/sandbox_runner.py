"""Hardened gVisor adapter for worker-controlled commands.

This executable intentionally requires a Docker daemon with the ``runsc``
runtime registered. It never falls back to runc.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from uuid import uuid4


def _bounded_int(value: Any, *, minimum: int, maximum: int, name: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not minimum <= result <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return result


def _read_payload() -> dict[str, Any]:
    value = json.load(sys.stdin)
    if not isinstance(value, dict):
        raise ValueError("sandbox request must be a JSON object")
    return value


def _runtime_available(docker: str) -> bool:
    probe = subprocess.run(
        [docker, "info", "--format", "{{json .Runtimes}}"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if probe.returncode != 0:
        return False
    try:
        runtimes = json.loads(probe.stdout)
    except json.JSONDecodeError:
        return False
    return "runsc" in runtimes


def execute(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("profile") != "gvisor":
        raise ValueError("sandbox profile must be gvisor")
    if payload.get("network_mode") != "egress-deny-all":
        raise ValueError("only egress-deny-all is supported")

    argv = payload.get("argv")
    if not isinstance(argv, list) or not argv or not all(isinstance(v, str) for v in argv):
        raise ValueError("argv must be a non-empty string array")
    root = Path(str(payload.get("workspace_root") or "")).resolve(strict=True)
    relative_cwd = Path(str(payload.get("cwd") or "."))
    if relative_cwd.is_absolute():
        raise ValueError("cwd must be relative to workspace_root")
    cwd = (root / relative_cwd).resolve(strict=True)
    cwd.relative_to(root)
    if not cwd.is_dir():
        raise ValueError("cwd must be a directory")

    timeout = _bounded_int(
        payload.get("timeout_seconds", 30), minimum=1, maximum=900, name="timeout_seconds"
    )
    output_limit = _bounded_int(
        payload.get("max_output_bytes", 64_000),
        minimum=1_024,
        maximum=2_000_000,
        name="max_output_bytes",
    )
    docker = shutil.which(os.getenv("AIAT_SANDBOX_DOCKER_BINARY", "docker"))
    if docker is None or not _runtime_available(docker):
        return {
            "available": False,
            "configured": True,
            "reason": "gvisor_runsc_runtime_not_available",
            "sandbox_profile": "gvisor",
        }

    image = os.getenv("AIAT_SANDBOX_IMAGE", "mas/tool-service:latest")
    container_name = f"aiat-sandbox-{uuid4().hex[:12]}"
    command = [
        docker,
        "run",
        "--rm",
        "--name",
        container_name,
        "--runtime=runsc",
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--pids-limit=256",
        "--memory=512m",
        "--cpus=0.5",
        "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=128m",
        "--env",
        "HOME=/tmp",
        "--env",
        "XDG_CONFIG_HOME=/tmp/.config",
        "--env",
        "SEMGREP_SETTINGS_FILE=/tmp/.semgrep/settings.yml",
        "--mount",
        f"type=bind,src={root},dst=/workspace",
        "--workdir",
        f"/workspace/{relative_cwd.as_posix()}",
        image,
        *argv,
    ]
    started = time.monotonic()
    with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
        process = subprocess.Popen(command, stdout=stdout_file, stderr=stderr_file)
        try:
            returncode: int | None = process.wait(timeout=timeout)
            timed_out = False
        except subprocess.TimeoutExpired:
            timed_out = True
            returncode = None
            process.kill()
            process.wait()
            subprocess.run(
                [docker, "rm", "--force", container_name],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
        stdout_size = stdout_file.tell()
        stderr_size = stderr_file.tell()
        stdout_file.seek(0)
        stderr_file.seek(0)
        stdout = stdout_file.read(output_limit)
        stderr = stderr_file.read(output_limit)

    return {
        "available": True,
        "returncode": returncode,
        "stdout": stdout[:output_limit].decode("utf-8", errors="replace"),
        "stderr": stderr[:output_limit].decode("utf-8", errors="replace"),
        "stdout_truncated": stdout_size > output_limit,
        "stderr_truncated": stderr_size > output_limit,
        "timed_out": timed_out,
        "duration_ms": round((time.monotonic() - started) * 1000, 2),
        "sandbox_profile": "gvisor",
        "network_mode": "egress-deny-all",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json-stdin", action="store_true", required=True)
    parser.parse_args()
    try:
        result = execute(_read_payload())
    except Exception as exc:
        result = {"available": False, "error": str(exc), "sandbox_profile": "gvisor"}
    json.dump(result, sys.stdout)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
