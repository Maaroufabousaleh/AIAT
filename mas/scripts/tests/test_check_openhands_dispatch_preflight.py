"""Non-secret tests for the morning OpenHands dispatch preflight."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _module():
    script = Path(__file__).resolve().parents[1] / "check_openhands_dispatch_preflight.py"
    spec = importlib.util.spec_from_file_location("check_openhands_dispatch_preflight", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_preflight_requires_secret_variables_and_explicit_sha() -> None:
    module = _module()
    root = Path(__file__).resolve().parents[2]
    workflow = (root.parent / ".github" / "workflows" / "openhands-candidate-certification.yml").read_text(encoding="utf-8")
    manifest = (root / "docs/provenance/openhands-candidate/2026-08-22-v1.43.0/worker-manifest.yaml").read_text(encoding="utf-8")
    sha = "a" * 40
    ready = module.evaluate_static(
        workflow_text=workflow,
        manifest_text=manifest,
        actual_sha=sha,
        requested_sha=sha,
        secret_names={"GROQ_API_KEY"},
        variable_values={"OPENHANDS_MODEL_ID": module.EXPECTED_MODEL, "OPENHANDS_MCP_SETTINGS_KEY": module.EXPECTED_MCP_KEY},
        local_tests_passed=True,
    )
    assert ready["ready_to_dispatch"] is True
    blocked = module.evaluate_static(
        workflow_text=workflow,
        manifest_text=manifest,
        actual_sha=sha,
        requested_sha=None,
        secret_names=set(),
        variable_values={},
        local_tests_passed=True,
    )
    assert blocked["ready_to_dispatch"] is False
    assert blocked["github_secret_presence"] == "NO"
    assert "GROQ_API_KEY" not in blocked.get("dispatch_command", "")


def test_dispatch_uses_workflow_ref_and_exact_candidate_input() -> None:
    module = _module()
    root = Path(__file__).resolve().parents[2]
    workflow = (root.parent / ".github" / "workflows" / "openhands-candidate-certification.yml").read_text(encoding="utf-8")
    manifest = (root / "docs/provenance/openhands-candidate/2026-08-22-v1.43.0/worker-manifest.yaml").read_text(encoding="utf-8")
    sha = "b" * 40
    report = module.evaluate_static(
        workflow_text=workflow,
        manifest_text=manifest,
        actual_sha=sha,
        requested_sha=sha,
        secret_names={"GROQ_API_KEY"},
        variable_values={"OPENHANDS_MODEL_ID": module.EXPECTED_MODEL, "OPENHANDS_MCP_SETTINGS_KEY": module.EXPECTED_MCP_KEY},
        local_tests_passed=True,
        workflow_ref="agent/fix-review-p1",
    )
    assert report["workflow_ref"] == "agent/fix-review-p1"
    assert "--ref agent/fix-review-p1" in report["dispatch_command"]
    assert f"-f candidate_sha={sha}" in report["dispatch_command"]
    assert f"--ref {sha}" not in report["dispatch_command"]


def test_candidate_sha_mismatch_is_reported_separately_from_missing_configuration() -> None:
    module = _module()
    root = Path(__file__).resolve().parents[2]
    workflow = (root.parent / ".github" / "workflows" / "openhands-candidate-certification.yml").read_text(encoding="utf-8")
    manifest = (root / "docs/provenance/openhands-candidate/2026-08-22-v1.43.0/worker-manifest.yaml").read_text(encoding="utf-8")
    report = module.evaluate_static(
        workflow_text=workflow,
        manifest_text=manifest,
        actual_sha="a" * 40,
        requested_sha="b" * 40,
        secret_names={"GROQ_API_KEY"},
        variable_values={"OPENHANDS_MODEL_ID": module.EXPECTED_MODEL, "OPENHANDS_MCP_SETTINGS_KEY": module.EXPECTED_MCP_KEY},
        local_tests_passed=True,
    )
    assert report["ready_to_dispatch"] is False
    assert report["checks"]["candidate_sha_frozen"] is False
    assert report["requested_candidate_sha"] == "b" * 40
    assert report["candidate_sha"] == "a" * 40
    assert report["blocking_reasons"] == ["CANDIDATE_SHA_MISMATCH"]


def test_preflight_reconciles_candidate_pins_across_provenance() -> None:
    module = _module()
    root = Path(__file__).resolve().parents[2]
    workflow = (root.parent / ".github" / "workflows" / "openhands-candidate-certification.yml").read_text(encoding="utf-8")
    manifest = (root / "docs/provenance/openhands-candidate/2026-08-22-v1.43.0/worker-manifest.yaml").read_text(encoding="utf-8")
    interface = (root / "docs/provenance/openhands-candidate/2026-08-22-v1.43.0/interface-verification.json").read_text(encoding="utf-8")
    provenance = (root / "docs/provenance/openhands-candidate/2026-08-22-v1.43.0/gateway-provenance.json").read_text(encoding="utf-8")
    report = module.evaluate_static(
        workflow_text=workflow,
        manifest_text=manifest,
        interface_text=interface.replace(module.EXPECTED_SOURCE_COMMIT, "0" * 40, 1),
        gateway_provenance_text=provenance,
        actual_sha="a" * 40,
        requested_sha="a" * 40,
        secret_names={"GROQ_API_KEY"},
        variable_values={"OPENHANDS_MODEL_ID": module.EXPECTED_MODEL, "OPENHANDS_MCP_SETTINGS_KEY": module.EXPECTED_MCP_KEY},
        local_tests_passed=True,
    )
    assert report["checks"]["candidate_pins_match"] is False


def test_skipped_local_tests_cannot_make_dispatch_ready() -> None:
    module = _module()
    root = Path(__file__).resolve().parents[2]
    workflow = (root.parent / ".github" / "workflows" / "openhands-candidate-certification.yml").read_text(encoding="utf-8")
    manifest = (root / "docs/provenance/openhands-candidate/2026-08-22-v1.43.0/worker-manifest.yaml").read_text(encoding="utf-8")
    sha = "a" * 40
    report = module.evaluate_static(
        workflow_text=workflow,
        manifest_text=manifest,
        actual_sha=sha,
        requested_sha=sha,
        secret_names={"GROQ_API_KEY"},
        variable_values={"OPENHANDS_MODEL_ID": module.EXPECTED_MODEL, "OPENHANDS_MCP_SETTINGS_KEY": module.EXPECTED_MCP_KEY},
        local_tests_passed=False,
    )
    assert report["ready_to_dispatch"] is False
    assert report["checks"]["local_deterministic_tests"] is False


def test_dispatch_preflight_covers_gateway_and_evidence_regressions() -> None:
    module = _module()
    required = {
        "scripts/tests/test_openhands_gateway_provenance.py",
        "scripts/tests/test_openhands_omniroute_auth.py",
        "scripts/tests/test_openhands_omniroute_readiness.py",
        "scripts/tests/test_check_openhands_evidence_schema.py",
    }
    assert required.issubset(set(module.LOCAL_TEST_COMMAND))
