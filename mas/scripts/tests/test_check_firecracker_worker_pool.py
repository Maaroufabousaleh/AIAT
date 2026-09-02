from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "check_firecracker_worker_pool.py"


def _module():
    spec = importlib.util.spec_from_file_location("check_firecracker_worker_pool", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_static_firecracker_pool_contract_passes_without_execution() -> None:
    module = _module()

    report = module.inspect_static()

    assert report["schema_version"] == "aiat.firecracker-worker-pool-readiness.v1"
    assert report["status"] == "pass"
    assert report["mutation_performed"] is False
    assert report["sandbox_execution_performed"] is False
    assert report["licence_metadata_is_gate"] is False
    assert report["launch_spec"]["network_mode"] == "egress-deny-all"
    assert report["launch_spec"]["cleanup"] is True


def test_live_firecracker_readiness_is_fail_closed_without_binaries(monkeypatch) -> None:
    module = _module()
    monkeypatch.setattr(module.shutil, "which", lambda _name: None)

    report = module.inspect_live()

    assert report["status"] == "blocked"
    assert report["mutation_performed"] is False
    assert report["sandbox_execution_performed"] is False
    assert "unavailable" in report["reason"]


def test_live_firecracker_readiness_requires_both_launcher_and_binary(monkeypatch) -> None:
    module = _module()
    monkeypatch.setattr(
        module.shutil,
        "which",
        lambda name: "/usr/local/bin/launcher" if name.startswith("aiat-") else None,
    )

    report = module.inspect_live()

    assert report["launcher_available"] is True
    assert report["firecracker_available"] is False
    assert report["status"] == "blocked"
    assert "Firecracker binary" in report["reason"]
