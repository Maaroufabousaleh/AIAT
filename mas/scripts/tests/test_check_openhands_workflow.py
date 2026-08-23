"""Static validation tests for the manual OpenHands workflow."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _workflow() -> str:
    return (Path(__file__).resolve().parents[2] / ".." / ".github" / "workflows" / "openhands-candidate-certification.yml").read_text(encoding="utf-8")


def _module():
    script = Path(__file__).resolve().parents[1] / "check_openhands_workflow.py"
    spec = importlib.util.spec_from_file_location("check_openhands_workflow", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_current_workflow_is_manual_pinned_and_fail_closed() -> None:
    report = _module().validate(_workflow())
    assert report["status"] == "PASS", report["errors"]
    assert report["manual_only"] is True
    assert report["candidate_sha_bound"] is True
    assert report["exact_image_pins"] is True
    assert "GITHUB_STEP_SUMMARY" in _workflow()
    assert "docker logs" not in _workflow()


def test_automatic_trigger_and_static_profile_are_rejected() -> None:
    text = _workflow().replace("workflow_dispatch:", "push:\n    branches: [main]\n  workflow_dispatch:", 1)
    text += "\n          OPENHANDS_AGENT_PROFILE_ID: ${{ vars.OPENHANDS_AGENT_PROFILE_ID }}\n"
    report = _module().validate(text)
    assert report["status"] == "FAILED_CERTIFICATION_IMPLEMENTATION"
    assert any(item.startswith("automatic_trigger_present:push") for item in report["errors"])
    assert "static_profile_uuid_input_present" in report["errors"]


def test_non_native_or_unpinned_candidate_inputs_are_rejected() -> None:
    text = _workflow()
    report = _module().validate(text.replace("runs-on: ubuntu-latest", "runs-on: ubuntu-slim", 1))
    assert "native_ubuntu_runner_requirement_missing" in report["errors"]
    report = _module().validate(text.replace("4c1237f391fe394e9f67505fe3a0bd2d81f84188", "0" * 40, 1))
    assert "openhands_source_commit_binding_missing" in report["errors"]


def test_candidate_images_require_linux_amd64_platform_readback() -> None:
    module = _module()
    text = _workflow()
    assert "candidate_image_platform_verification_missing" not in module.validate(text)["errors"]
    weakened = text.replace('test "$omniroute_platform" = "linux/amd64"', "true", 1)
    assert "candidate_image_platform_verification_missing" in module.validate(weakened)["errors"]


def test_live_task_requires_host_test_and_workspace_verification() -> None:
    module = _module()
    text = _workflow()
    assert "postrun_task_verification_missing" not in module.validate(text)["errors"]
    weakened = text.replace('            --host-workspace "$RUNNER_TEMP/aiat-openhands-workspace" \\\n', "", 1)
    assert "postrun_task_verification_missing" in module.validate(weakened)["errors"]


def test_live_task_requires_a_disposable_git_baseline_for_real_diff() -> None:
    module = _module()
    text = _workflow()
    assert "coding_task_git_baseline_missing" not in module.validate(text)["errors"]
    weakened = text.replace('git -C "$RUNNER_TEMP/aiat-openhands-workspace" commit --quiet -m "certification baseline"', "true", 1)
    assert "coding_task_git_baseline_missing" in module.validate(weakened)["errors"]


def test_live_certification_invokes_the_lifecycle_wave() -> None:
    module = _module()
    text = _workflow()
    assert "live_lifecycle_wave_missing" not in module.validate(text)["errors"]
    assert "live_lifecycle_wave_missing" in module.validate(text.replace("            --exercise-lifecycle \\\n", "", 1))["errors"]


def test_missing_provider_gate_or_cleanup_readback_is_rejected() -> None:
    text = _workflow()
    report = _module().validate(text.replace("steps.provider_preflight.outputs.ready == 'true'", "steps.other.outputs.ready == 'true'"))
    assert "provider_gate_missing_before_expensive_stages" in report["errors"]

    report = _module().validate(text.replace("steps.preflight.outputs.ready == 'true'", "steps.other.outputs.ready == 'true'"))
    assert "candidate_preflight_gate_missing_before_expensive_stages" in report["errors"]

    report = _module().validate(text.replace('"verified_absent"', '"removed"'))
    assert "cleanup_absence_readback_missing" in report["errors"]

    report = _module().validate(text.replace("preflight-wrapper.json", "preflight.json"))
    assert "provider_preflight_execution_failure_class_missing" in report["errors"]

    report = _module().validate(text + "\n          docker logs --tail 1 container\n")
    assert "raw_container_logs_retained" in report["errors"]


def test_provider_and_gateway_failures_gate_worker_startup() -> None:
    module = _module()
    text = _workflow()
    assert "provider_route_gate_missing_before_model_stages" not in module.validate(text)["errors"]
    assert "gateway_route_gate_missing_before_worker_stages" not in module.validate(text)["errors"]

    without_provider_gate = text.replace(" && steps.provider.outputs.ready == 'true'", "")
    assert "provider_route_gate_missing_before_model_stages" in module.validate(without_provider_gate)["errors"]

    without_gateway_gate = text.replace(" && steps.gateway.outputs.ready == 'true'", "")
    assert "gateway_route_gate_missing_before_worker_stages" in module.validate(without_gateway_gate)["errors"]


def test_gateway_network_topology_must_be_explicitly_asserted() -> None:
    module = _module()
    text = _workflow()
    assert "network_topology_assertion_missing" not in module.validate(text)["errors"]
    weakened = text.replace('test "$topology_status" = PASS', 'echo "$topology_status"')
    assert "network_topology_assertion_missing" in module.validate(weakened)["errors"]

    weakened = text.replace('"aliases": sorted(str(item) for item in (value.get("Aliases") or []) if item),', '"aliases": [],', 1)
    assert "network_topology_assertion_missing" in module.validate(weakened)["errors"]


def test_cleanup_requires_delete_status_and_absence_readback() -> None:
    module = _module()
    text = _workflow()
    assert "cleanup_absence_readback_missing" not in module.validate(text)["errors"]
    weakened = text.replace('delete_ok=false', 'delete_status_unchecked=false', 1)
    report = module.validate(weakened)
    assert "cleanup_absence_readback_missing" in report["errors"]


def test_cleanup_requires_workspace_and_tool_image_absence() -> None:
    module = _module()
    text = _workflow()
    assert "workspace_or_tool_image_cleanup_missing" not in module.validate(text)["errors"]
    weakened = text.replace('rm -rf -- "$RUNNER_TEMP/aiat-openhands-workspace"', "true", 1)
    report = module.validate(weakened)
    assert "workspace_or_tool_image_cleanup_missing" in report["errors"]
