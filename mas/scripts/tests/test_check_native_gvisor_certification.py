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
    assert '[[ "$CANDIDATE_SHA" =~ ^[0-9a-fA-F]{40}$ ]]' in workflow
    assert 'test "$checked_out" = "$CANDIDATE_SHA"' in workflow
    assert "ubuntu:24.04" not in workflow
    assert "@sha256:33ceb71981b602c1a7443a53469e4dba065f7503eab3078a2d7a57a2ab987517" in workflow
    assert "--hello-image '${{ steps.smoke.outputs.image }}'" in workflow


def test_image_inputs_require_immutable_digest_references():
    module = _module()
    assert module._repo_digest("ubuntu:24.04") is None
    assert module._repo_digest("hello-world:latest") is None
    pinned = "ubuntu@sha256:" + "a" * 64
    assert module._repo_digest(pinned) == pinned


def test_certification_does_not_execute_mutable_image_inputs(monkeypatch):
    module = _module()
    monkeypatch.setattr(module, "_git_revision", lambda: "a" * 40)
    monkeypatch.setattr(module.shutil, "which", lambda _name: "/usr/bin/runsc")
    monkeypatch.setattr(module, "_command_result", lambda *_args, **_kwargs: {"status": "pass"})
    monkeypatch.setattr(module, "_docker_json", lambda _format: ({"runsc": {}}, None))
    monkeypatch.setattr(module, "_remaining_named_containers", lambda _name: 0)
    monkeypatch.setattr(module, "_run_sandbox_suite", lambda _image: (_ for _ in ()).throw(AssertionError("must not run")))

    report = module.certify(smoke_image="ubuntu:24.04", hello_image="hello-world:latest")

    assert report["status"] == "blocked"
    assert "smoke image must be an immutable digest reference" in report["blockers"]
    assert "hello image must be an immutable digest reference" in report["blockers"]
    assert report["checks"]["hello_world_runsc"]["status"] == "blocked"
