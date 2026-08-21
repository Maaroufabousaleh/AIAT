"""Regression checks for the bounded native gVisor evidence boundary."""

from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "check_native_gvisor_certification.py"


def _module():
    spec = importlib.util.spec_from_file_location("check_native_gvisor_certification", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_certification_requires_native_linux_and_immutable_smoke(monkeypatch):
    module = _module()
    monkeypatch.setattr(module.platform, "system", lambda: "Linux")
    monkeypatch.setattr(module.platform, "release", lambda: "6.6.87.2-microsoft-standard-WSL2")
    monkeypatch.setattr(module.shutil, "which", lambda name: None)
    monkeypatch.setattr(module, "_docker_json", lambda _format: (None, "docker_engine_unavailable"))
    monkeypatch.setattr(module, "_repo_digest", lambda _image: None)
    monkeypatch.setattr(module, "_command_result", lambda *_args, **_kwargs: {"status": "blocked"})
    monkeypatch.setattr(module, "_run_sandbox_suite", lambda _image: {"status": "blocked", "reason": "no runtime"})
    monkeypatch.setattr(module, "_remaining_named_containers", lambda _name: 0)

    report = module.certify(smoke_image="ubuntu:24.04")

    assert report["status"] == "blocked"
    assert "host is not a native Linux release host" in report["blockers"]
    assert "runsc binary is unavailable" in report["blockers"]
    assert report["checks"]["cleanup"]["zero_residue_verified"] is True
    assert report["licence_metadata_is_gate"] is False


def test_workflow_uses_standard_linux_runner_and_manual_dispatch():
    workflow = (SCRIPT.parents[2] / ".github" / "workflows" / "native-linux-gvisor-certification.yml").read_text(
        encoding="utf-8"
    )
    assert "workflow_dispatch:" in workflow
    assert "runs-on: ubuntu-latest" in workflow
    assert "ubuntu-slim" in workflow
    assert "sudo runsc install" in workflow
    assert "check_native_gvisor_certification.py" in workflow
