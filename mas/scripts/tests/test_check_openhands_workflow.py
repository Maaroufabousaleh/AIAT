"""Static validation tests for the manual OpenHands workflow."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import yaml


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
    assert report["run_block_shell_validation"] == "PASS"
    assert report["heredoc_audit"] == "PASS"
    assert "GITHUB_STEP_SUMMARY" in _workflow()
    assert "EXPECTED_FAIL_CLOSED_CERTIFICATION_BLOCK" in _workflow()
    assert "OmniRoute API auth boundary" in _workflow()
    assert "docker logs" not in _workflow()


def test_candidate_helper_contract_gate_blocks_stale_checkouts_before_expensive_stages() -> None:
    module = _module()
    text = _workflow()
    report = module.validate(text)
    assert "candidate_helper_contract_gate_missing" not in report["errors"]
    assert "candidate_helper_contract_gate_not_propagated" not in report["errors"]

    weakened = text.replace(
        " && steps.candidate_contract.outputs.ready == 'true'\n        run: |\n          set -euo pipefail\n          docker pull",
        "\n        run: |\n          set -euo pipefail\n          docker pull",
        1,
    )
    assert any(
        item.startswith("candidate_helper_contract_gate_not_propagated")
        for item in module.validate(weakened)["errors"]
    )

    self_referencing = text.replace(
        "if: steps.provider_preflight.outputs.ready == 'true' && steps.preflight.outputs.ready == 'true'\n        continue-on-error: true\n        run: |\n          set +e\n          output=",
        "if: steps.provider_preflight.outputs.ready == 'true' && steps.preflight.outputs.ready == 'true' && steps.candidate_contract.outputs.ready == 'true'\n        continue-on-error: true\n        run: |\n          set +e\n          output=",
        1,
    )
    assert "candidate_helper_contract_self_reference" in module.validate(self_referencing)["errors"]


def test_agent_server_capability_controls_are_explicit_and_scoped() -> None:
    module = _module()
    text = _workflow()
    assert "openhands_server_capability_controls_missing" not in module.validate(text)["errors"]
    weakened = text.replace("--env OH_ENABLE_VSCODE=false \\\n", "", 1)
    assert "openhands_server_capability_controls_missing" in module.validate(weakened)["errors"]


def test_all_workflow_run_blocks_have_valid_shell_syntax() -> None:
    document = yaml.safe_load(_workflow())
    assert isinstance(document, dict)
    jobs = document.get("jobs") or {}
    assert isinstance(jobs, dict)
    for job in jobs.values():
        for step in (job.get("steps") or []):
            run = step.get("run") if isinstance(step, dict) else None
            if not isinstance(run, str):
                continue
            result = subprocess.run(["bash", "-n"], input=run, text=True, capture_output=True, check=False)
            assert result.returncode == 0, f"{step.get('name')}: {result.stderr}"


def test_broken_heredoc_is_rejected_even_when_bash_n_returns_zero() -> None:
    module = _module()
    text = _workflow()
    broken = text.replace(
        "\n          PY\n          uv run python scripts/verify_openhands_gateway_pins.py",
        "\n          uv run python scripts/verify_openhands_gateway_pins.py",
        1,
    )
    report = module.validate(broken)
    assert report["status"] == "FAILED_CERTIFICATION_IMPLEMENTATION"
    assert any(
        "heredoc_marker_count_mismatch:PY" in error or "unclosed_heredoc:PY" in error
        for error in report["errors"]
    )

    # Bash -n alone is insufficient: it warns but returns zero for this case.
    run = next(
        step["run"]
        for step in (yaml.safe_load(broken)["jobs"]["certify"]["steps"])
        if step.get("name") == "Pull pinned certification images and verify gateway provenance"
    )
    parsed = subprocess.run(["bash", "-n"], input=run, text=True, capture_output=True, check=False)
    assert parsed.returncode == 0
    assert "here-document" in parsed.stderr
    warning_issues = module._run_block_shell_issues("cat <<'PY'\necho hi\n")
    assert "bash_unclosed_heredoc_warning" in warning_issues


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


def test_gateway_source_provenance_is_delegated_to_diagnostic_helper() -> None:
    module = _module()
    text = _workflow()
    assert "gateway_provenance_diagnostic_helper_missing" not in module.validate(text)["errors"]
    helper = (Path(__file__).resolve().parents[1] / "verify_openhands_gateway_provenance.py").read_text(encoding="utf-8")
    assert 'f"refs/tags/{tag}^{{}}"' in helper
    assert 'tag_type = "LIGHTWEIGHT"' in helper
    weakened = text.replace("scripts/verify_openhands_gateway_provenance.py", "scripts/missing_gateway_provenance.py", 1)
    assert "gateway_provenance_diagnostic_helper_missing" in module.validate(weakened)["errors"]


def test_gateway_source_archives_are_checksum_pinned() -> None:
    module = _module()
    text = _workflow()
    assert "gateway_provenance_diagnostic_helper_missing" not in module.validate(text)["errors"]
    helper = (Path(__file__).resolve().parents[1] / "verify_openhands_gateway_provenance.py").read_text(encoding="utf-8")
    assert "3e6474f2d7f507b124158291e327f995886756573d90dc641c04d73afea45ede" in helper
    assert "e81fc85f47204ffe09cd283a56cfce92f109a6f13de7d3bef3f4057f7f43d2e6" in helper
    weakened = text.replace("scripts/verify_openhands_gateway_provenance.py", "scripts/missing_gateway_provenance.py", 1)
    assert "gateway_provenance_diagnostic_helper_missing" in module.validate(weakened)["errors"]


def test_candidate_images_require_linux_amd64_platform_readback() -> None:
    module = _module()
    text = _workflow()
    assert "candidate_image_platform_verification_missing" not in module.validate(text)["errors"]
    weakened = text.replace('test "$omniroute_platform" = "linux/amd64"', "true", 1)
    assert "candidate_image_platform_verification_missing" in module.validate(weakened)["errors"]


def test_gateway_ports_are_loopback_only_and_internal_target_is_not_host_bound() -> None:
    module = _module()
    text = _workflow()
    assert "non_loopback_port_exposure" not in module.validate(text)["errors"]
    assert "laptop_or_host_gateway_dependency" not in module.validate(text)["errors"]
    weakened = text.replace("--publish 127.0.0.1:4000:4000", "--publish 0.0.0.0:4000:4000", 1)
    assert "non_loopback_port_exposure" in module.validate(weakened)["errors"]
    weakened = text.replace("OPENHANDS_MODEL_GATEWAY_URL: http://litellm:4000", "OPENHANDS_MODEL_GATEWAY_URL: http://127.0.0.1:4000", 1)
    assert "laptop_or_host_gateway_dependency" in module.validate(weakened)["errors"]


def test_omniroute_readiness_and_api_port_contract_are_explicit() -> None:
    module = _module()
    text = _workflow()
    report = module.validate(text)
    assert report["status"] == "PASS", report["errors"]
    assert "api/monitoring/health" in text
    assert "check_openhands_omniroute_readiness.py" in text
    assert "--publish 127.0.0.1:20129:20129" in text
    assert "--env REQUIRE_API_KEY=true" in text
    assert "steps.omniroute_auth.outputs.ready == 'true'" in text
    config = Path(__file__).resolve().parents[2] / "infra" / "compose" / "litellm_openhands_certification.yaml"
    assert "http://omniroute:20129/v1" in config.read_text(encoding="utf-8")
    assert "model: openai/auto/coding" in config.read_text(encoding="utf-8")


def test_omniroute_auth_probe_requires_bounded_transport_retry() -> None:
    module = _module()
    text = _workflow()
    assert "omniroute_auth_transport_retry_missing" not in module.validate(text)["errors"]
    weakened = text.replace("            --attempts 30 \\\n", "", 1).replace("            --interval-seconds 1\n", "", 1)
    assert "omniroute_auth_transport_retry_missing" in module.validate(weakened)["errors"]


def test_omniroute_auth_probe_is_compatible_with_frozen_candidate_helpers() -> None:
    module = _module()
    text = _workflow()
    report = module.validate(text)
    assert "omniroute_auth_candidate_compatibility_missing" not in report["errors"]
    weakened = text.replace('auth_probe_mode="compatibility_outer_retry"', 'auth_probe_mode="missing"', 1)
    assert "omniroute_auth_candidate_compatibility_missing" in module.validate(weakened)["errors"]


def test_network_topology_reads_aliases_from_each_container() -> None:
    module = _module()
    text = _workflow()
    report = module.validate(text)
    assert "network_topology_alias_readback_missing" not in report["errors"]
    weakened = text.replace('"container_alias_readback": alias_readback,', '"container_alias_readback": {},', 1)
    assert "network_topology_alias_readback_missing" in module.validate(weakened)["errors"]


def test_network_topology_failure_cannot_unlock_live_stages() -> None:
    module = _module()
    text = _workflow()
    report = module.validate(text)
    assert "network_topology_assertion_missing" not in report["errors"]

    weakened = text.replace(
        '            echo "status=BLOCKED_NETWORK_TOPOLOGY" >> "$GITHUB_OUTPUT"\n',
        "",
        1,
    )
    report = module.validate(weakened)
    assert "network_topology_assertion_missing" in report["errors"]

    broken = text.replace(
        '''          if [ "$topology_status" = PASS ]; then
            echo "ready=true" >> "$GITHUB_OUTPUT"
            echo "status=PASS" >> "$GITHUB_OUTPUT"
          else
            # This step intentionally stays on the evidence/cleanup path, but
            # a failed alias/IP readback must never unlock profile materializa-
            # tion or the live adapter wave.
            echo "ready=false" >> "$GITHUB_OUTPUT"
            echo "status=BLOCKED_NETWORK_TOPOLOGY" >> "$GITHUB_OUTPUT"
          fi
''',
        '''          test "$topology_status" = PASS
          echo "ready=true" >> "$GITHUB_OUTPUT"
          echo "status=PASS" >> "$GITHUB_OUTPUT"
''',
        1,
    )
    report = module.validate(broken)
    assert "network_topology_assertion_missing" in report["errors"]

    weakened = text.replace(
        '            echo "ready=false" >> "$GITHUB_OUTPUT"\n'
        '            echo "status=BLOCKED_NETWORK_TOPOLOGY" >> "$GITHUB_OUTPUT"\n',
        '            echo "status=BLOCKED_NETWORK_TOPOLOGY" >> "$GITHUB_OUTPUT"\n',
        1,
    )
    report = module.validate(weakened)
    assert "network_topology_assertion_missing" in report["errors"]


def test_live_task_test_environment_scrubs_controller_secrets(tmp_path: Path, monkeypatch) -> None:
    module = _module()
    text = _workflow()
    assert "live_task_environment_secret_scrub_missing" not in module.validate(text)["errors"]
    helper = module.LIVE_CERTIFICATION_HELPER.read_text(encoding="utf-8")
    weakened = helper.replace("env=_task_test_environment()", "env=dict(os.environ)", 1)
    helper_path = tmp_path / "openhands_live_certify.py"
    helper_path.write_text(weakened, encoding="utf-8")
    monkeypatch.setattr(module, "LIVE_CERTIFICATION_HELPER", helper_path)
    assert "live_task_environment_secret_scrub_missing" in module.validate(text)["errors"]


def test_runsc_uses_explicit_run_scoped_name_resolution() -> None:
    module = _module()
    text = _workflow()
    report = module.validate(text)
    assert "runsc_network_name_resolution_contract_missing" not in report["errors"]
    weakened = text.replace('--add-host "litellm:${litellm_ip}" \\\n', "", 1)
    assert "runsc_network_name_resolution_contract_missing" in module.validate(weakened)["errors"]


def test_network_topology_parser_uses_container_network_settings_aliases(tmp_path: Path) -> None:
    document = yaml.safe_load(_workflow())
    step = next(
        step
        for step in document["jobs"]["certify"]["steps"]
        if step.get("name") == "Record disposable gateway network topology"
    )
    run = step["run"]
    python_body = run.split("<<'PY'\n", 1)[1].split("\nPY", 1)[0]
    script = tmp_path / "topology.py"
    script.write_text(python_body, encoding="utf-8")
    network = "aiat-openhands-cert-network-123"
    run_id = "123"
    names = {
        "aiat-openhands-cert-123": "agent-server",
        "aiat-openhands-tool-service-123": "tool-service",
        "aiat-openhands-litellm-123": "litellm",
        "aiat-openhands-omniroute-123": "omniroute",
    }
    topology = [
        {
            "Containers": {
                name: {"Name": name, "IPv4Address": f"172.30.0.{index + 2}"}
                for index, name in enumerate(names)
            }
        }
    ]
    topology_input = tmp_path / "network.json"
    topology_input.write_text(json.dumps(topology), encoding="utf-8")
    output = tmp_path / "report.json"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "name = sys.argv[-1]\n"
        f"aliases = {names!r}\n"
        f"print(json.dumps({{\"{network}\": {{\"Aliases\": [aliases[name]]}}}}))\n",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)
    env = dict(os.environ, PATH=f"{fake_bin}:{os.environ.get('PATH', '')}")
    result = subprocess.run(
        [sys.executable, str(script), str(output), str(topology_input), run_id, "0", network],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["topology_status"] == "PASS"
    assert report["alias_inspect_status"] == "PASS"
    assert report["network_scope_status"] == "PASS"
    assert report["container_alias_readback"]["aiat-openhands-litellm-123"]["attached_networks"] == [network]
    assert report["container_alias_readback"]["aiat-openhands-litellm-123"]["aliases"] == ["litellm"]


def test_network_topology_rejects_extra_container_network_attachment(tmp_path: Path) -> None:
    document = yaml.safe_load(_workflow())
    step = next(
        step
        for step in document["jobs"]["certify"]["steps"]
        if step.get("name") == "Record disposable gateway network topology"
    )
    run = step["run"]
    python_body = run.split("<<'PY'\n", 1)[1].split("\nPY", 1)[0]
    script = tmp_path / "topology.py"
    script.write_text(python_body, encoding="utf-8")
    network = "aiat-openhands-cert-network-123"
    run_id = "123"
    names = {
        "aiat-openhands-cert-123": "agent-server",
        "aiat-openhands-tool-service-123": "tool-service",
        "aiat-openhands-litellm-123": "litellm",
        "aiat-openhands-omniroute-123": "omniroute",
    }
    topology_input = tmp_path / "network.json"
    topology_input.write_text(
        json.dumps([{"Containers": {name: {"Name": name, "IPv4Address": "172.30.0.2"} for name in names}}]),
        encoding="utf-8",
    )
    output = tmp_path / "report.json"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "name = sys.argv[-1]\n"
        f"extra = {{'{network}': {{'Aliases': ['service']}}, 'unapproved-network': {{'Aliases': [name]}}}}\n"
        "print(json.dumps(extra if name.endswith('litellm-123') else {list(extra)[0]: extra[list(extra)[0]]}))\n",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)
    env = dict(os.environ, PATH=f"{fake_bin}:{os.environ.get('PATH', '')}")
    result = subprocess.run(
        [sys.executable, str(script), str(output), str(topology_input), run_id, "0", network],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["network_scope_status"] == "BLOCKED"
    assert report["topology_status"] == "BLOCKED"


def test_auto_router_and_deterministic_baseline_are_both_required() -> None:
    module = _module()
    text = _workflow()
    report = module.validate(text)
    assert "provider_baseline_gate_missing" not in report["errors"]
    assert "litellm_auto_coding_route_missing" not in report["errors"]
    weakened = text.replace(
        "          uv run python scripts/check_openhands_provider_baseline.py \\\n",
        "          uv run python scripts/missing.py \\\n",
        1,
    )
    assert "provider_baseline_gate_missing" in module.validate(weakened)["errors"]
    weakened = text.replace(
        '--auto-routing-output "$RUNNER_TEMP/aiat-openhands-evidence/gateway/auto-routing.json"',
        '--missing-auto-routing-output "$RUNNER_TEMP/aiat-openhands-evidence/gateway/auto-routing.json"',
        1,
    )
    assert "auto_routing_evidence_missing" in module.validate(weakened)["errors"]


def test_auto_router_scope_helper_is_required(tmp_path: Path, monkeypatch) -> None:
    module = _module()
    text = _workflow()
    report = module.validate(text)
    assert "provider_scope_helper_contract_missing" not in report["errors"]
    helper = Path(__file__).resolve().parents[1] / "provision_openhands_certification_gateway.py"
    weakened_helper = helper.read_text(encoding="utf-8").replace(
        '"omniroute_noauth_provider_scope_readback_mismatch"',
        '"scope_readback_missing"',
        1,
    )
    helper_path = tmp_path / "provision_openhands_certification_gateway.py"
    helper_path.write_text(weakened_helper, encoding="utf-8")
    monkeypatch.setattr(module, "PROVIDER_SCOPE_HELPER", helper_path)
    report = module.validate(text)
    assert "provider_scope_helper_contract_missing" in report["errors"]

    independent_helper = helper.read_text(encoding="utf-8").replace(
        '            "baseline_gate_independent": True,\n',
        "",
        1,
    )
    helper_path.write_text(independent_helper, encoding="utf-8")
    report = module.validate(text)
    assert "provider_scope_helper_contract_missing" in report["errors"]


def test_gateway_route_probe_cli_contract_is_checked(tmp_path: Path, monkeypatch) -> None:
    module = _module()
    helper = Path(__file__).resolve().parents[1] / "check_openhands_certification_gateway.py"
    weakened_helper = helper.read_text(encoding="utf-8").replace(
        '    parser.add_argument(\n        "--auto-routing-output",\n        type=Path,\n        help="Optional second path for the governed auto/coding scalar evidence.",\n    )\n',
        "",
        1,
    )
    helper_path = tmp_path / "check_openhands_certification_gateway.py"
    helper_path.write_text(weakened_helper, encoding="utf-8")
    monkeypatch.setattr(module, "GATEWAY_ROUTE_PROBE", helper_path)
    report = module.validate(_workflow())
    assert "gateway_route_probe_cli_contract_missing" in report["errors"]


def test_provider_baseline_probe_cli_contract_is_checked(tmp_path: Path, monkeypatch) -> None:
    module = _module()
    helper = Path(__file__).resolve().parents[1] / "check_openhands_provider_baseline.py"
    weakened_helper = helper.read_text(encoding="utf-8").replace(
        '    parser.add_argument("--retry-delay-seconds", type=float, default=DEFAULT_RETRY_DELAY_SECONDS)\n',
        "",
        1,
    )
    helper_path = tmp_path / "check_openhands_provider_baseline.py"
    helper_path.write_text(weakened_helper, encoding="utf-8")
    monkeypatch.setattr(module, "PROVIDER_BASELINE_PROBE", helper_path)
    report = module.validate(_workflow())
    assert "provider_baseline_probe_cli_contract_missing" in report["errors"]


def test_workflow_script_references_must_exist_in_candidate() -> None:
    module = _module()
    text = _workflow().replace(
        "scripts/check_openhands_provider_baseline.py",
        "scripts/missing_provider_baseline.py",
        1,
    )
    report = module.validate(text)
    assert "workflow_script_missing:scripts/missing_provider_baseline.py" in report["errors"]


def test_baseline_failure_does_not_suppress_independent_auto_router_evidence() -> None:
    module = _module()
    text = _workflow()
    report = module.validate(text)
    assert "baseline_blocks_auto_router_evidence" not in report["errors"]
    marker = "        id: litellm\n"
    weakened = text.replace(
        marker,
        marker + "        if: steps.baseline.outputs.ready == 'true'\n",
        1,
    )
    report = module.validate(weakened)
    assert "baseline_blocks_auto_router_evidence" in report["errors"]


def test_gate_matrix_receives_provider_and_candidate_status_outputs() -> None:
    module = _module()
    text = _workflow()
    assert "gate_matrix_status_wiring_missing" not in module.validate(text)["errors"]
    weakened = text.replace(
        "PROVIDER_STATUS: ${{ steps.provider_preflight.outputs.status }}",
        "PROVIDER_STATUS: ${{ steps.other.outputs.status }}",
        1,
    )
    report = module.validate(weakened)
    assert "gate_matrix_status_wiring_missing" in report["errors"]


def test_provider_baseline_requires_bounded_retry_contract() -> None:
    module = _module()
    text = _workflow()
    assert "provider_baseline_gate_missing" not in module.validate(text)["errors"]
    weakened = text.replace("              --max-attempts 2 \\\n", "", 1)
    assert "provider_baseline_gate_missing" in module.validate(weakened)["errors"]


def test_wrong_omniroute_management_port_is_rejected_by_static_validation(tmp_path: Path, monkeypatch) -> None:
    module = _module()
    config = tmp_path / "litellm_openhands_certification.yaml"
    config.write_text("api_base: http://omniroute:20128/v1\n", encoding="utf-8")
    monkeypatch.setattr(module, "LITELLM_CERTIFICATION_CONFIG", config)
    report = module.validate(_workflow())
    assert "litellm_omniroute_management_port_used_for_api" in report["errors"]


def test_live_task_requires_host_test_and_workspace_verification() -> None:
    module = _module()
    text = _workflow()
    assert "postrun_task_verification_missing" not in module.validate(text)["errors"]
    weakened = text.replace('            --host-workspace "$RUNNER_TEMP/aiat-openhands-workspace" \\\n', "", 1)
    assert "postrun_task_verification_missing" in module.validate(weakened)["errors"]


def test_live_task_requires_a_skill_and_plugin_free_certification_workspace() -> None:
    module = _module()
    text = _workflow()
    assert "certification_workspace_skill_boundary_missing" not in module.validate(text)["errors"]
    weakened = text.replace("workspace-skill-sources.json", "workspace-policy.json", 1)
    assert "certification_workspace_skill_boundary_missing" in module.validate(weakened)["errors"]


def test_live_task_requires_a_disposable_git_baseline_for_real_diff() -> None:
    module = _module()
    text = _workflow()
    assert "coding_task_git_baseline_missing" not in module.validate(text)["errors"]
    weakened = text.replace('git -C "$RUNNER_TEMP/aiat-openhands-workspace" commit --quiet -m "certification baseline"', "true", 1)
    assert "coding_task_git_baseline_missing" in module.validate(weakened)["errors"]


def test_cleanup_removes_generated_secret_environment_entries() -> None:
    module = _module()
    text = _workflow()
    assert "run_scoped_secret_environment_cleanup_missing" not in module.validate(text)["errors"]
    weakened = text.replace("-e '/^AIAT_TOOL_SECRET=/d' \\", "-e '/^AIAT_TOOL_SECRET=/x' \\", 1)
    assert "run_scoped_secret_environment_cleanup_missing" in module.validate(weakened)["errors"]


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


def test_cleanup_readback_covers_v143_nested_agent_settings_envelope() -> None:
    module = _module()
    text = _workflow()
    assert "cleanup_absence_readback_missing" not in module.validate(text)["errors"]
    assert "cleanup_absence_readback_missing" in module.validate(
        text.replace('          agent_settings = settings.get("agent_settings") if isinstance(settings, dict) else None\n', "", 1)
    )["errors"]


def test_cleanup_readback_merges_direct_and_nested_mcp_maps(tmp_path: Path) -> None:
    module = _module()
    text = _workflow()
    document = yaml.safe_load(text)
    step = next(
        step
        for step in document["jobs"]["certify"]["steps"]
        if step.get("name") == "Verify disposable cleanup"
    )
    run = step["run"]
    python_body = run.split("<<'PY'\n", 1)[1].split("\nPY", 1)[0]
    script = tmp_path / "cleanup.py"
    script.write_text(python_body, encoding="utf-8")
    output = tmp_path / "mcp-cleanup.json"
    env = dict(
        os.environ,
        DELETE_CODE="204",
        SETTINGS_CODE="200",
        SETTINGS_JSON=json.dumps(
            {
                "mcp_config": {},
                "agent_settings": {
                    "mcp_config": {"aiat-openhands-test-run": {"url": "redacted"}}
                },
            }
        ),
        MCP_KEY="aiat-openhands-test-run",
    )
    result = subprocess.run([sys.executable, str(script), str(output)], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["verified_absent"] is False

    weakened = text.replace("config.update(candidate)", "config = candidate", 1)
    assert "cleanup_absence_readback_missing" in module.validate(weakened)["errors"]


def test_evidence_schema_validation_is_required_before_workflow_pass() -> None:
    module = _module()
    text = _workflow()
    assert "evidence_schema_validation_missing" not in module.validate(text)["errors"]
    weakened = text.replace("test \"$schema_status\" = PASS", "echo \"$schema_status\"", 1)
    assert "evidence_schema_validation_missing" in module.validate(weakened)["errors"]


def test_provider_and_gateway_failures_gate_worker_startup() -> None:
    module = _module()
    text = _workflow()
    assert "provider_route_gate_missing_before_model_stages" not in module.validate(text)["errors"]
    assert "gateway_route_gate_missing_before_worker_stages" not in module.validate(text)["errors"]

    without_provider_gate = text.replace(" && steps.provider.outputs.ready == 'true'", "")
    assert "provider_route_gate_missing_before_model_stages" in module.validate(without_provider_gate)["errors"]

    without_gateway_gate = text.replace(" && steps.gateway.outputs.ready == 'true'", "")
    assert "gateway_route_gate_missing_before_worker_stages" in module.validate(without_gateway_gate)["errors"]


def test_network_creation_is_evidenced_and_gates_downstream_stages() -> None:
    module = _module()
    text = _workflow()
    assert "network_readiness_gate_missing" not in module.validate(text)["errors"]
    weakened = text.replace("network-startup.json", "network-startup-omitted.json", 1)
    assert "network_readiness_gate_missing" in module.validate(weakened)["errors"]


def test_service_startup_failures_have_scalar_failure_classes() -> None:
    module = _module()
    text = _workflow()
    assert "startup_failure_classification_missing" not in module.validate(text)["errors"]
    weakened = text.replace("TOOL_SERVICE_STARTUP_FAILURE", "TOOL_SERVICE_STARTUP_OMITTED", 1)
    assert "startup_failure_classification_missing" in module.validate(weakened)["errors"]


def test_agent_server_health_failure_publishes_blocked_output() -> None:
    module = _module()
    text = _workflow()
    assert "agent_server_health_output_missing" not in module.validate(text)["errors"]
    weakened = text.replace(
        '            echo "status=BLOCKED_GVISOR" >> "$GITHUB_OUTPUT"\n            exit 1',
        "            exit 1",
        1,
    )
    report = module.validate(weakened)
    assert "agent_server_health_output_missing" in report["errors"]


def test_gateway_network_topology_must_be_explicitly_asserted() -> None:
    module = _module()
    text = _workflow()
    assert "network_topology_assertion_missing" not in module.validate(text)["errors"]
    weakened = text.replace('if [ "$topology_status" = PASS ]; then', 'echo "$topology_status"', 1)
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


def test_cleanup_handles_stopped_agent_container_by_disposal() -> None:
    module = _module()
    text = _workflow()
    assert "stopped_agent_container_cleanup_missing" not in module.validate(text)["errors"]
    weakened = text.replace('agent_running=false', 'agent_state_unknown=false', 1)
    report = module.validate(weakened)
    assert "stopped_agent_container_cleanup_missing" in report["errors"]


def test_cleanup_requires_workspace_and_tool_image_absence() -> None:
    module = _module()
    text = _workflow()
    assert "workspace_profile_or_tool_image_cleanup_missing" not in module.validate(text)["errors"]
    weakened = text.replace('rm -rf -- "$RUNNER_TEMP/aiat-openhands-workspace"', "true", 1)
    report = module.validate(weakened)
    assert "workspace_profile_or_tool_image_cleanup_missing" in report["errors"]

    weakened = text.replace(
        'tool_image="aiat-openhands-tool-service-cert:${GITHUB_RUN_ID}"',
        'tool_image="aiat-openhands-tool-service-cert-${GITHUB_RUN_ID}"',
        1,
    )
    report = module.validate(weakened)
    assert "workspace_profile_or_tool_image_cleanup_missing" in report["errors"]


def test_cleanup_requires_run_scoped_profile_disposal_evidence() -> None:
    module = _module()
    text = _workflow()
    assert "workspace_profile_or_tool_image_cleanup_missing" not in module.validate(text)["errors"]
    weakened = text.replace('"verified_by_agent_container_absence": profile_status in {"NOT_CREATED", "VERIFIED_BY_CONTAINER_DISPOSAL"} and containers_absent == "true",', '"profile_disposal": True,', 1)
    assert "workspace_profile_or_tool_image_cleanup_missing" in module.validate(weakened)["errors"]
