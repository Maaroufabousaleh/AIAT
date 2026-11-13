from __future__ import annotations

import json
import subprocess
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from scripts.native_docker_engine import EngineError, inspect_engine, start_engine, stop_engine

if TYPE_CHECKING:
    from collections.abc import Sequence


class EngineCommands:
    def __init__(
        self,
        *,
        info: dict[str, object] | None = None,
        active_units: set[str] | None = None,
        running_containers: str = "",
        dockerd: str | None = "/usr/bin/dockerd --config-file=/etc/docker/aiat-daemon.json\n",
    ) -> None:
        self.info = info or {
            "ID": "test-native-engine-id",
            "ServerVersion": "29.8.1",
            "DockerRootDir": "/var/lib/docker-aiat",
            "Runtimes": {"runsc": {}, "runc": {}},
        }
        self.active_units = active_units if active_units is not None else {"aiat-docker.service"}
        self.running_containers = running_containers
        self.dockerd = dockerd
        self.started_units: list[str] = []
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, args: Sequence[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        command = tuple(args)
        self.calls.append(command)
        if command[:2] == ("systemctl", "is-active"):
            active = command[2] in self.active_units
            return subprocess.CompletedProcess(command, 0 if active else 3, "active\n" if active else "inactive\n", "")
        if command[:2] == ("systemctl", "start") or command[:4] == ("sudo", "-n", "systemctl", "start"):
            unit = command[-1]
            self.started_units.append(unit)
            self.active_units.add(unit)
            if unit == "aiat-docker.service" and self.dockerd is None:
                self.dockerd = "/usr/bin/dockerd --config-file=/etc/docker/aiat-daemon.json\n"
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[:2] == ("systemctl", "stop") or command[:4] == ("sudo", "-n", "systemctl", "stop"):
            self.active_units.discard(command[-1])
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[:3] == ("ps", "-eo", "args="):
            return subprocess.CompletedProcess(command, 0, self.dockerd or "", "")
        if len(command) >= 3 and command[0] == "/usr/bin/docker" and command[1:3] == ("--host", "unix:///run/aiat-docker/docker.sock"):
            if command[3:] == ("ps", "--quiet"):
                return subprocess.CompletedProcess(command, 0, self.running_containers, "")
            return subprocess.CompletedProcess(command, 0, json.dumps(self.info), "")
        raise AssertionError(f"unexpected command: {command}")


def test_status_verifies_dedicated_socket_root_and_runsc_without_starting_services() -> None:
    commands = EngineCommands()
    with patch("scripts.native_docker_engine.platform.system", return_value="Linux"), patch(
        "scripts.native_docker_engine.shutil.which", return_value="/usr/bin/docker"
    ):
        report = inspect_engine(commands)

    assert report["status"] == "ready"
    assert report["engine_id"] == "test-native-engine-id"
    assert report["docker_root"] == "/var/lib/docker-aiat"
    assert report["socket"] == "unix:///run/aiat-docker/docker.sock"
    assert report["application_stack_started"] is False
    assert commands.started_units == []


def test_status_fails_if_the_standard_docker_daemon_is_active() -> None:
    commands = EngineCommands(active_units={"aiat-docker.service", "docker.service"})
    with patch("scripts.native_docker_engine.platform.system", return_value="Linux"), pytest.raises(
        EngineError, match="competing_system_docker_unit_active"
    ), patch("scripts.native_docker_engine.shutil.which", return_value="/usr/bin/docker"):
        inspect_engine(commands)


def test_status_fails_if_data_root_or_runsc_is_missing() -> None:
    wrong_root = EngineCommands(info={
        "ID": "test-native-engine-id",
        "DockerRootDir": "/var/lib/docker",
        "Runtimes": {"runsc": {}},
    })
    no_runsc = EngineCommands(info={
        "ID": "test-native-engine-id",
        "DockerRootDir": "/var/lib/docker-aiat",
        "Runtimes": {"runc": {}},
    })
    with patch("scripts.native_docker_engine.platform.system", return_value="Linux"), patch(
        "scripts.native_docker_engine.shutil.which", return_value="/usr/bin/docker"
    ):
        with pytest.raises(EngineError, match="native_docker_data_root_mismatch"):
            inspect_engine(wrong_root)
        with pytest.raises(EngineError, match="runsc_runtime_unavailable"):
            inspect_engine(no_runsc)


def test_start_only_starts_the_native_daemon_once_and_then_verifies_it() -> None:
    commands = EngineCommands(active_units=set(), dockerd=None)
    with patch("scripts.native_docker_engine.platform.system", return_value="Linux"), patch(
        "scripts.native_docker_engine.shutil.which", return_value="/usr/bin/docker"
    ), patch("scripts.native_docker_engine.os.geteuid", return_value=0, create=True):
        report = start_engine(timeout_seconds=5, runner=commands, sleep=lambda _seconds: None)

    assert commands.started_units == ["aiat-docker.service"]
    assert report["application_stack_started"] is False
    assert sum(call[:2] == ("systemctl", "start") for call in commands.calls) == 1


def test_start_refuses_an_unmanaged_daemon_before_starting_native_service() -> None:
    commands = EngineCommands(active_units=set(), dockerd="/usr/local/bin/dockerd --host=tcp://127.0.0.1:2375\n")
    with patch("scripts.native_docker_engine.platform.system", return_value="Linux"), pytest.raises(
        EngineError, match="unmanaged_dockerd_process_present"
    ):
        start_engine(timeout_seconds=5, runner=commands)

    assert commands.started_units == []


def test_stop_refuses_to_stop_daemon_while_any_container_is_running() -> None:
    commands = EngineCommands(running_containers="container-id\n")
    with patch("scripts.native_docker_engine.platform.system", return_value="Linux"), patch(
        "scripts.native_docker_engine.shutil.which", return_value="/usr/bin/docker"
    ), pytest.raises(EngineError, match="running_containers_present"):
        stop_engine(confirmed_idle=True, runner=commands)

    assert not any(call[:2] == ("systemctl", "stop") for call in commands.calls)


def test_stop_requires_explicit_idle_confirmation() -> None:
    with pytest.raises(EngineError, match="operator_idle_confirmation_required"):
        stop_engine(confirmed_idle=False, runner=EngineCommands())


def test_stop_only_stops_native_daemon_when_no_containers_are_running() -> None:
    commands = EngineCommands()
    with patch("scripts.native_docker_engine.platform.system", return_value="Linux"), patch(
        "scripts.native_docker_engine.shutil.which", return_value="/usr/bin/docker"
    ), patch("scripts.native_docker_engine.os.geteuid", return_value=0, create=True):
        report = stop_engine(confirmed_idle=True, runner=commands)

    assert report["status"] == "stopped"
    assert commands.started_units == []
    assert sum(call[:2] == ("systemctl", "stop") for call in commands.calls) == 1
