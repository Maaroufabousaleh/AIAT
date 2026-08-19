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


def _runtime_probe(docker: str) -> tuple[bool, str]:
    """Return a scalar runtime result without exposing Docker error text."""
    try:
        probe = subprocess.run(
            [docker, "info", "--format", "{{json .Runtimes}}"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        return False, "gvisor_runtime_probe_timeout"
    except OSError:
        return False, "gvisor_runtime_probe_failed"
    if probe.returncode != 0:
        return False, "gvisor_runtime_probe_failed"
    try:
        runtimes = json.loads(probe.stdout)
    except json.JSONDecodeError:
        return False, "gvisor_runtime_probe_invalid_response"
    if not isinstance(runtimes, dict):
        return False, "gvisor_runtime_probe_invalid_response"
    if "runsc" not in runtimes:
        return False, "gvisor_runsc_runtime_not_available"
    return True, ""


def _runtime_available(docker: str) -> bool:
    """Compatibility helper for callers that only need the boolean result."""
    return _runtime_probe(docker)[0]


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
    workspace_read_only = payload.get("workspace_read_only", False)
    if not isinstance(workspace_read_only, bool):
        raise ValueError("workspace_read_only must be a boolean")
    docker = shutil.which(os.getenv("AIAT_SANDBOX_DOCKER_BINARY", "docker"))
    if docker is None:
        return {
            "available": False,
            "configured": True,
            "reason": "gvisor_docker_cli_not_available",
            "sandbox_profile": "gvisor",
        }
    runtime_available, runtime_reason = _runtime_probe(docker)
    if not runtime_available:
        return {
            "available": False,
            "configured": True,
            "reason": runtime_reason,
            "sandbox_profile": "gvisor",
        }

    image = os.getenv("AIAT_SANDBOX_IMAGE", "mas/tool-service:latest")
    container_name = f"aiat-sandbox-{uuid4().hex[:12]}"
    mount = f"type=bind,src={root},dst=/workspace"
    if workspace_read_only:
        mount += ",readonly"
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
        "--user=10001:10001",
        "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=128m",
        "--env",
        "HOME=/tmp",
        "--env",
        "XDG_CONFIG_HOME=/tmp/.config",
        "--env",
        "XDG_CACHE_HOME=/tmp/.cache",
        "--env",
        "PATH=/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/bin:/sbin",
        "--env",
        "PYTHONNOUSERSITE=1",
        "--env",
        "PYTHONDONTWRITEBYTECODE=1",
        "--env",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1",
        "--env",
        "SEMGREP_SETTINGS_FILE=/tmp/.semgrep/settings.yml",
        "--mount",
        mount,
        "--workdir",
        f"/workspace/{relative_cwd.as_posix()}",
        image,
        *argv,
    ]
    started = time.monotonic()
    with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
        try:
            process = subprocess.Popen(command, stdout=stdout_file, stderr=stderr_file)
        except OSError:
            return {
                "available": False,
                "configured": True,
                "reason": "gvisor_container_launch_failed",
                "sandbox_profile": "gvisor",
            }
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
    except ValueError:
        result = {
            "available": False,
            "configured": True,
            "reason": "sandbox_request_invalid",
            "sandbox_profile": "gvisor",
        }
    except (OSError, subprocess.SubprocessError):
        result = {
            "available": False,
            "configured": True,
            "reason": "sandbox_runtime_error",
            "sandbox_profile": "gvisor",
        }
    except Exception:
        result = {
            "available": False,
            "configured": True,
            "reason": "sandbox_execution_error",
            "sandbox_profile": "gvisor",
        }
    json.dump(result, sys.stdout)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
