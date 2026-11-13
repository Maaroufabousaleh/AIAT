#!/usr/bin/env python3
"""Start or verify AIAT's native Docker daemon without starting app writers."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shlex
import shutil
import subprocess
import time
from collections.abc import Callable, Sequence
from typing import Any

DOCKER_SOCKET = "unix:///run/aiat-docker/docker.sock"
DOCKER_ROOT = "/var/lib/docker-aiat"
DAEMON_CONFIG = "/etc/docker/aiat-daemon.json"
SERVICE = "aiat-docker.service"
OTHER_UNITS = ("docker.service", "docker.socket")

CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


class EngineError(RuntimeError):
    """Raised when the native engine is unavailable or does not match policy."""


def _run(args: Sequence[str], runner: CommandRunner) -> subprocess.CompletedProcess[str]:
    try:
        return runner(list(args), check=False, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise EngineError(f"command_unavailable:{args[0]}") from exc


def _active(unit: str, runner: CommandRunner) -> bool:
    result = _run(("systemctl", "is-active", unit), runner)
    return result.returncode == 0 and result.stdout.strip() == "active"


def _dockerd_processes(runner: CommandRunner) -> list[str]:
    result = _run(("ps", "-eo", "args="), runner)
    if result.returncode != 0:
        raise EngineError("process_inventory_unavailable")
    processes = []
    for line in result.stdout.splitlines():
        try:
            argv = shlex.split(line)
        except ValueError as exc:
            raise EngineError("process_inventory_invalid") from exc
        if argv and os.path.basename(argv[0]) == "dockerd":
            processes.append(line.strip())
    return processes


def inspect_engine(runner: CommandRunner = subprocess.run) -> dict[str, Any]:
    """Read service/process/daemon state and require the dedicated engine only."""
    if platform.system() != "Linux":
        raise EngineError("linux_host_required")
    if not _active(SERVICE, runner):
        raise EngineError("native_docker_service_inactive")
    active_other = [unit for unit in OTHER_UNITS if _active(unit, runner)]
    if active_other:
        raise EngineError("competing_system_docker_unit_active")

    dockerd = _dockerd_processes(runner)
    if len(dockerd) != 1 or DAEMON_CONFIG not in dockerd[0]:
        raise EngineError("native_dockerd_process_identity_mismatch")

    docker = shutil.which("docker")
    if docker is None:
        raise EngineError("docker_cli_unavailable")
    result = _run(
        (
            docker,
            "--host",
            DOCKER_SOCKET,
            "info",
            "--format",
            "{{json .}}",
        ),
        runner,
    )
    if result.returncode != 0:
        raise EngineError("native_docker_api_unavailable")
    try:
        info = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise EngineError("native_docker_info_invalid") from exc

    if info.get("DockerRootDir") != DOCKER_ROOT:
        raise EngineError("native_docker_data_root_mismatch")
    runtimes = info.get("Runtimes")
    if not isinstance(runtimes, dict) or "runsc" not in runtimes:
        raise EngineError("runsc_runtime_unavailable")
    engine_id = info.get("ID")
    if not isinstance(engine_id, str) or not engine_id:
        raise EngineError("native_docker_engine_id_missing")

    return {
        "status": "ready",
        "engine_id": engine_id,
        "server_version": info.get("ServerVersion"),
        "docker_root": DOCKER_ROOT,
        "socket": DOCKER_SOCKET,
        "runtime_runsc": "registered",
        "application_stack_started": False,
        "desktop_dependency_checked": False,
    }


def start_engine(*, timeout_seconds: int, runner: CommandRunner = subprocess.run, sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """Start only aiat-docker.service once, then poll its explicit API socket."""
    if platform.system() != "Linux":
        raise EngineError("linux_host_required")
    if _active(SERVICE, runner):
        return inspect_engine(runner)

    active_other = [unit for unit in OTHER_UNITS if _active(unit, runner)]
    if active_other:
        raise EngineError("competing_system_docker_unit_active")
    if _dockerd_processes(runner):
        raise EngineError("unmanaged_dockerd_process_present")

    command = ("systemctl", "start", SERVICE) if os.geteuid() == 0 else ("sudo", "-n", "systemctl", "start", SERVICE)
    started = _run(command, runner)
    if started.returncode != 0:
        raise EngineError("native_docker_service_start_failed")

    deadline = time.monotonic() + timeout_seconds
    last_error = "native_docker_api_unavailable"
    while time.monotonic() < deadline:
        try:
            return inspect_engine(runner)
        except EngineError as exc:
            last_error = str(exc)
            sleep(0.5)
    raise EngineError(f"native_docker_start_timeout:{last_error}")


def stop_engine(*, confirmed_idle: bool, runner: CommandRunner = subprocess.run) -> dict[str, Any]:
    """Stop only the native daemon after explicit confirmation and container check."""
    if not confirmed_idle:
        raise EngineError("operator_idle_confirmation_required")
    report = inspect_engine(runner)
    docker = shutil.which("docker")
    if docker is None:
        raise EngineError("docker_cli_unavailable")
    running = _run((docker, "--host", DOCKER_SOCKET, "ps", "--quiet"), runner)
    if running.returncode != 0:
        raise EngineError("running_container_check_failed")
    if running.stdout.strip():
        raise EngineError("running_containers_present")

    command = ("systemctl", "stop", SERVICE) if os.geteuid() == 0 else ("sudo", "-n", "systemctl", "stop", SERVICE)
    stopped = _run(command, runner)
    if stopped.returncode != 0 or _active(SERVICE, runner):
        raise EngineError("native_docker_service_stop_failed")
    return {**report, "status": "stopped", "application_stack_started": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("status", help="read and verify the native daemon without starting it")
    start = subparsers.add_parser("start", help="start only aiat-docker.service and verify readiness")
    start.add_argument("--timeout-seconds", type=int, default=45)
    stop = subparsers.add_parser("stop", help="stop the native daemon after checking all Docker work is idle")
    stop.add_argument("--confirm-idle", action="store_true", required=True)
    args = parser.parse_args()
    if args.command == "start" and not 5 <= args.timeout_seconds <= 120:
        parser.error("--timeout-seconds must be between 5 and 120")
    try:
        if args.command == "status":
            report = inspect_engine()
        elif args.command == "start":
            report = start_engine(timeout_seconds=args.timeout_seconds)
        else:
            report = stop_engine(confirmed_idle=args.confirm_idle)
    except EngineError as exc:
        print(json.dumps({"status": "blocked", "reason": str(exc), "application_stack_started": False}, sort_keys=True))
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
