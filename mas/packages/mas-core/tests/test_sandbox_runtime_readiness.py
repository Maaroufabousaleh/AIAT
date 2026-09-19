"""Static and fail-closed Docker sandbox readiness evidence."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import yaml

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "check_sandbox_runtime_readiness.py"
COMPOSE = Path(__file__).resolve().parents[3] / "infra" / "compose" / "docker-compose.yml"


def test_static_sandbox_declarations_pass() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--json"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["schema_version"] == "aiat.sandbox-runtime-readiness.v1"
    assert report["status"] == "pass"
    assert report["worker_count"] == 39
    assert report["hardened_worker_count"] > 0
    assert report["opencode_runtime"]["status"] == "pass"
    assert report["opencode_runtime"]["network"] == ["internal"]
    assert report["opencode_runtime"]["read_only"] is True
    assert report["opencode_runtime"]["cap_drop_all"] is True
    assert report["opencode_runtime"]["no_new_privileges"] is True
    assert "live" not in report


def test_static_sandbox_contract_rejects_unsafe_opencode_runtime(tmp_path: Path) -> None:
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    service = compose["services"]["opencode-runtime"]
    service["read_only"] = False
    service["networks"] = ["internal", "workers"]
    service["cap_drop"] = []
    path = tmp_path / "compose.yml"
    path.write_text(yaml.safe_dump(compose, sort_keys=False), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--compose", str(path), "--json"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert report["status"] == "fail"
    errors = " ".join(report["errors"])
    assert "networks must be exactly" in errors
    assert "read_only must be true" in errors
    assert "cap_drop must include ALL" in errors


def test_live_sandbox_readiness_is_blocked_without_docker_engine(monkeypatch) -> None:
    """Keep the fail-closed case deterministic on hosts that have Docker."""

    import importlib.util

    spec = importlib.util.spec_from_file_location("check_sandbox_runtime_readiness", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    monkeypatch.setattr(module, "_docker_runtimes", lambda: (set(), "Docker Engine unavailable"))
    report = module.inspect_live()

    assert report["status"] == "blocked"
    assert report["sandbox_profile"] == "sandboxed"
    assert report["sandbox_class"] == "sandboxed"
    assert report["reason"] == "Docker Engine unavailable"


def test_live_sandbox_probe_requires_runsc_and_separates_smoke(monkeypatch) -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location("check_sandbox_runtime_readiness", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    monkeypatch.setattr(module, "_docker_runtimes", lambda: ({"runc"}, None))
    missing = module.inspect_live()
    assert missing["status"] == "blocked"
    assert "no runc fallback" in missing["reason"]

    monkeypatch.setattr(module, "_docker_runtimes", lambda: ({"runsc"}, None))
    registered = module.inspect_live()
    assert registered["status"] == "pass"
    assert registered["smoke"] == "not_checked"

    smoke = module.inspect_live(smoke=True)
    assert smoke["status"] == "blocked"
    assert "requires --image" in smoke["reason"]

    monkeypatch.setattr(module, "_docker_runtimes", lambda: ({"kata-qemu"}, None))
    monkeypatch.setattr(
        module,
        "_kata_host_prerequisites",
        lambda: {"status": "READY", "vmm": "qemu"},
    )
    monkeypatch.setattr(
        module,
        "_run_kata_smoke",
        lambda image, *, runtime: (
            True,
            f"Kata ({runtime}) QEMU guest smoke completed",
            {"runtime": runtime, "guest_kernel": "kata-guest"},
        ),
    )
    kata = module.inspect_live(
        require_kata=True,
        image="example/smoke@sha256:" + "a" * 64,
    )
    assert kata["status"] == "pass"
    assert kata["sandbox_class"] == "vm_isolated"
    assert kata["kata"] == "available"
    assert kata["vm_isolated_status"] == "AVAILABLE"
    assert kata["kata_guest_kernel"] == "kata-guest"


def test_kata_smoke_uses_the_discovered_runtime_name(monkeypatch) -> None:
    import importlib.util
    import subprocess

    spec = importlib.util.spec_from_file_location("check_sandbox_runtime_readiness", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    commands: list[list[str]] = []

    def fake_run(command: list[str], *, timeout: float):
        del timeout
        commands.append(command)
        if "create" in command:
            return subprocess.CompletedProcess(command, 0, "kata-container-id\n", "")
        if "inspect" in command:
            return subprocess.CompletedProcess(command, 0, "kata-qemu\n", "")
        if "exec" in command:
            return subprocess.CompletedProcess(command, 0, "kata-guest-kernel\n", "")
        return subprocess.CompletedProcess(command, 0, "0\n", "")

    monkeypatch.setattr(module, "_run", fake_run)
    monkeypatch.setattr(module.platform, "release", lambda: "wsl-host-kernel")

    passed, reason, evidence = module._run_kata_smoke(
        "example/smoke@sha256:" + "a" * 64,
        runtime="kata-qemu",
    )

    assert passed is True
    assert reason == "Kata (kata-qemu) QEMU guest smoke completed"
    assert evidence["guest_kernel"] == "kata-guest-kernel"
    assert "--runtime=kata-qemu" in commands[0]


def test_optional_kata_unavailable_does_not_block_runsc(monkeypatch) -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location("check_sandbox_runtime_readiness", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    monkeypatch.setattr(module, "_docker_runtimes", lambda: ({"runsc"}, None))
    report = module.inspect_live(check_kata=True)

    assert report["status"] == "pass"
    assert report["sandbox_runtime"] == "runsc"
    assert report["kata_status"] == "OPTIONAL_UNAVAILABLE"
    assert report["vm_isolated_status"] == "OPTIONAL_UNAVAILABLE"


def test_required_kata_unavailable_fails_closed(monkeypatch) -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location("check_sandbox_runtime_readiness", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    monkeypatch.setattr(module, "_docker_runtimes", lambda: ({"runsc"}, None))
    report = module.inspect_live(
        require_kata=True,
        image="example/smoke@sha256:" + "a" * 64,
    )

    assert report["status"] == "blocked"
    assert report["kata_status"] == "REQUIRED_UNAVAILABLE"
    assert report["vm_isolated_status"] == "REQUIRED_UNAVAILABLE"
    assert "no Kata Docker runtime" in report["reason"]


def test_optional_kata_smoke_failure_does_not_block_runsc(monkeypatch) -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location("check_sandbox_runtime_readiness", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    monkeypatch.setattr(module, "_docker_runtimes", lambda: ({"runsc", "kata"}, None))
    monkeypatch.setattr(
        module,
        "_kata_host_prerequisites",
        lambda: {"status": "READY", "vmm": "qemu"},
    )
    monkeypatch.setattr(
        module,
        "_run_kata_smoke",
        lambda image, *, runtime: (False, "Kata guest smoke failed", {"runtime": runtime}),
    )
    monkeypatch.setattr(module, "_run_smoke", lambda image, *, runtime: (True, "runsc smoke passed"))

    report = module.inspect_live(
        check_kata=True,
        smoke=True,
        image="example/smoke@sha256:" + "a" * 64,
    )

    assert report["status"] == "pass"
    assert report["sandbox_runtime"] == "runsc"
    assert report["kata_status"] == "FAILED"
    assert report["vm_isolated_status"] == "FAILED"
    assert report["warnings"] == ["Kata guest smoke failed"]
