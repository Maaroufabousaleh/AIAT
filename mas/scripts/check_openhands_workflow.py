"""Static, fail-closed validation for the manual OpenHands workflow."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

SCHEMA = "aiat.openhands-certification-workflow-validation.v1"
WORKFLOW_NAME = ".github/workflows/openhands-candidate-certification.yml"
LITELLM_CERTIFICATION_CONFIG = Path(__file__).resolve().parents[1] / "infra" / "compose" / "litellm_openhands_certification.yaml"
EXPECTED_IMAGES = (
    "ghcr.io/openhands/agent-server:1.43.0-python@sha256:36f847d1dfbbbdce90052437b06a3c6e76b8a54683228182eaf73085f03fcd97",
    "ghcr.io/berriai/litellm@sha256:a50b02a6056095da29308310bb608f0509e08ddcd1d105bae9c21007d82b0e95",
    "diegosouzapw/omniroute@sha256:ceae8d9da0acf075dbf5905b61c9ae32e749112650fcf7f4434c8d96ac6d3ebb",
)

_HEREDOC_OPEN = re.compile(
    r"(?<!<)<<(?P<strip>-?)(?P<quote>['\"\\]?)(?P<marker>[A-Za-z_][A-Za-z0-9_]*)(?P=quote)"
)


def _heredoc_issues(run: str) -> list[str]:
    """Find heredoc delimiters in the exact YAML-rendered shell block.

    ``bash -n`` returns success for an unterminated heredoc while emitting a
    warning, so delimiter accounting is intentionally performed separately.
    The queue follows shell's left-to-right heredoc body order and does not
    inspect body text as shell, preventing nested-looking content from hiding
    a missing terminator.
    """

    pending: list[tuple[str, bool, int]] = []
    opener_counts: Counter[str] = Counter()
    terminator_counts: Counter[str] = Counter()
    issues: list[str] = []
    for line_number, line in enumerate(run.splitlines(), start=1):
        # Count marker lines independently of the state machine.  If an
        # earlier terminator is missing, a later same-named marker can be
        # consumed by the wrong heredoc; this count catches that exact shell
        # swallowing failure even when ``bash -n`` returns zero.
        if line and not line.startswith((" ", "\t")):
            terminator_counts[line] += 1
        matches = list(_HEREDOC_OPEN.finditer(line))
        for match in matches:
            opener_counts[match.group("marker")] += 1
        if pending:
            marker, allow_tabs, opener_line = pending[0]
            if line == marker or (allow_tabs and line.startswith("\t") and line.lstrip("\t") == marker):
                pending.pop(0)
            continue
        for match in matches:
            marker = match.group("marker")
            pending.append((marker, bool(match.group("strip")), line_number))
    for marker, opener_count in opener_counts.items():
        terminator_count = terminator_counts.get(marker, 0)
        if opener_count != terminator_count:
            issues.append(
                f"heredoc_marker_count_mismatch:{marker}:openers={opener_count}:terminators={terminator_count}"
            )
    for marker, _allow_tabs, opener_line in pending:
        issues.append(f"unclosed_heredoc:{marker}:opener_line={opener_line}")
    return issues


def _run_block_shell_issues(run: str) -> list[str]:
    """Parse one GitHub Actions ``run: |`` value exactly as shell text."""

    issues = _heredoc_issues(run)
    result = subprocess.run(
        ["bash", "-n"],
        input=run,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        issues.append(f"bash_syntax_error:{result.stderr.strip() or 'unknown'}")
    # Bash deliberately reports an unterminated heredoc as a warning and may
    # still return zero under ``-n``.  Treat that warning as a hard failure.
    if re.search(r"here-document .*delimited by end-of-file", result.stderr, re.IGNORECASE):
        issues.append("bash_unclosed_heredoc_warning")
    return issues


def _workflow_run_block_issues(text: str) -> list[str]:
    """Validate the actual YAML-rendered ``run`` blocks, not reconstructed snippets."""

    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return [f"workflow_yaml_parse_error:{type(exc).__name__}"]
    if not isinstance(document, dict):
        return ["workflow_yaml_root_invalid"]
    jobs = document.get("jobs") or {}
    if not isinstance(jobs, dict):
        return ["workflow_jobs_invalid"]
    issues: list[str] = []
    for job_id, job in jobs.items():
        if not isinstance(job, dict):
            issues.append(f"workflow_job_invalid:{job_id}")
            continue
        steps = job.get("steps") or []
        if not isinstance(steps, list):
            issues.append(f"workflow_steps_invalid:{job_id}")
            continue
        for index, step in enumerate(steps):
            if not isinstance(step, dict) or not isinstance(step.get("run"), str):
                continue
            label = str(step.get("name") or f"step-{index}")
            for issue in _run_block_shell_issues(step["run"]):
                issues.append(f"run_block:{job_id}:{label}:{issue}")
    return issues


def _gateway_provenance_helper_issues() -> list[str]:
    """Ensure the delegated helper still carries the exact candidate pins."""

    helper_path = Path(__file__).with_name("verify_openhands_gateway_provenance.py")
    try:
        helper = helper_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ["gateway_provenance_helper_missing"]
    required = (
        '"version": "1.90.0"',
        '"version": "3.8.38"',
        '"source_commit": "6e8282d40655d47ed1557f030e53d6819e464e79"',
        '"source_commit": "7b139fdb5e42658a49f9d99ddf0eeeba9a994fd8"',
        '"source_archive_sha256": "3e6474f2d7f507b124158291e327f995886756573d90dc641c04d73afea45ede"',
        '"source_archive_sha256": "e81fc85f47204ffe09cd283a56cfce92f109a6f13de7d3bef3f4057f7f43d2e6"',
        'f"refs/tags/{tag}"',
        'f"refs/tags/{tag}^{{}}"',
        'tag_type = "ANNOTATED"',
        'tag_type = "LIGHTWEIGHT"',
    )
    return ["gateway_provenance_helper_pin_or_tag_logic_missing"] if any(item not in helper for item in required) else []


def _omniroute_readiness_helper_issues() -> list[str]:
    """Ensure readiness uses the pinned public health and narrow classes."""

    helper_path = Path(__file__).with_name("check_openhands_omniroute_readiness.py")
    try:
        helper = helper_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ["omniroute_readiness_helper_missing"]
    required = (
        'EXPECTED_HEALTH_PATH = "/api/monitoring/health"',
        '"OMNIROUTE_HEALTH_AUTH_CONTRACT_FAILURE"',
        '"OMNIROUTE_APPLICATION_HEALTH_FAILURE"',
        '"OMNIROUTE_HEALTH_TIMEOUT"',
        '"OMNIROUTE_STARTUP_FAILURE"',
        '"raw_response_retained": False',
    )
    return ["omniroute_readiness_helper_contract_missing"] if any(item not in helper for item in required) else []


def _omniroute_auth_helper_issues() -> list[str]:
    """Ensure the provider API auth boundary is tested without retaining keys."""

    helper_path = Path(__file__).with_name("check_openhands_omniroute_auth.py")
    try:
        helper = helper_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ["omniroute_auth_helper_missing"]
    required = (
        '"unauthenticated_provider_route_denied"',
        '"wrong_gateway_key_denied"',
        '"correct_gateway_key_accepted"',
        '"credentials_retained": False',
        "def _status_with_retry(",
        '"MODEL_GATEWAY_TRANSPORT_FAILURE"',
    )
    return ["omniroute_auth_helper_contract_missing"] if any(item not in helper for item in required) else []


def _litellm_omniroute_port_issues() -> list[str]:
    """Reject a LiteLLM route aimed at OmniRoute's management port."""

    try:
        config = LITELLM_CERTIFICATION_CONFIG.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ["litellm_certification_config_missing"]
    if "api_base: http://omniroute:20128/v1" in config:
        return ["litellm_omniroute_management_port_used_for_api"]
    if "api_base: http://omniroute:20129/v1" not in config:
        return ["litellm_omniroute_api_port_missing"]
    if "model: openai/auto/coding" not in config:
        return ["litellm_auto_coding_route_missing"]
    return []


def validate(text: str) -> dict[str, Any]:
    errors: list[str] = []
    run_block_issues = _workflow_run_block_issues(text)
    errors.extend(run_block_issues)
    if not re.search(r"(?m)^on:\s*$", text):
        errors.append("workflow_on_key_missing")
    if not re.search(r"(?m)^\s+workflow_dispatch:\s*$", text):
        errors.append("workflow_dispatch_missing")
    if "runs-on: ubuntu-latest" not in text or re.search(r"(?m)^\s*runs-on:\s*ubuntu-slim\s*$", text):
        errors.append("native_ubuntu_runner_requirement_missing")
    for trigger in ("push:", "pull_request:", "schedule:", "repository_dispatch:"):
        if re.search(rf"(?m)^\s+{re.escape(trigger)}", text):
            errors.append(f"automatic_trigger_present:{trigger[:-1]}")
    if "actions/checkout@v4" not in text or "ref: ${{ inputs.candidate_sha }}" not in text:
        errors.append("exact_candidate_checkout_missing")
    if "actual=$(git rev-parse HEAD)" not in text or 'test "$actual" = "$EXPECTED_SHA"' not in text:
        errors.append("candidate_sha_binding_missing")
    if (
        "image-platforms.json" not in text
        or 'test "$openhands_platform" = "linux/amd64"' not in text
        or 'test "$litellm_platform" = "linux/amd64"' not in text
        or 'test "$omniroute_platform" = "linux/amd64"' not in text
    ):
        errors.append("candidate_image_platform_verification_missing")
    if "4c1237f391fe394e9f67505fe3a0bd2d81f84188" not in text:
        errors.append("openhands_source_commit_binding_missing")
    if (
        "scripts/verify_openhands_gateway_provenance.py" not in text
        or "--litellm-image \"$LITELLM_IMAGE\"" not in text
        or "--omniroute-image \"$OMNIROUTE_IMAGE\"" not in text
        or "gateway/version-verification.json" not in text
    ):
        errors.append("gateway_provenance_diagnostic_helper_missing")
    errors.extend(_gateway_provenance_helper_issues())
    errors.extend(_omniroute_readiness_helper_issues())
    errors.extend(_omniroute_auth_helper_issues())
    if "--attempts 30" not in text or "--interval-seconds 1" not in text:
        errors.append("omniroute_auth_transport_retry_missing")
    # The workflow checks out the exact candidate SHA before invoking helper
    # scripts.  Keep the auth step compatible with older, valid candidates
    # whose helper predates the native retry flags: it must probe --help,
    # provide a bounded outer retry fallback, and retain scalar mode/count
    # evidence rather than passing unknown CLI arguments.
    compatibility_markers = (
        'auth_probe_mode="compatibility_outer_retry"',
        'auth_probe_mode="native_transport_retry"',
        'scripts/check_openhands_omniroute_auth.py --help',
        'workflow_probe_mode',
        'workflow_attempt_count',
    )
    if any(marker not in text for marker in compatibility_markers):
        errors.append("omniroute_auth_candidate_compatibility_missing")
    errors.extend(_litellm_omniroute_port_issues())
    for image in EXPECTED_IMAGES:
        if image not in text:
            errors.append(f"exact_image_pin_missing:{image.split('@', 1)[0]}")
    image_lines = [line for line in text.splitlines() if "IMAGE:" in line or "IMAGE=" in line]
    if any("latest" in line.lower() or "@sha256:" not in line for line in image_lines):
        errors.append("floating_or_unpinned_image_reference")
    if "--runtime=runsc" not in text or "sudo runsc install" not in text:
        errors.append("runsc_proof_missing")
    if re.search(r"--publish\s+(?!127\.0\.0\.1:)", text):
        errors.append("non_loopback_port_exposure")
    if "host.docker.internal" in text or "OPENHANDS_MODEL_GATEWAY_URL: http://127.0.0.1" in text:
        errors.append("laptop_or_host_gateway_dependency")
    if "id: provider_preflight" not in text or "GROQ_API_KEY" not in text:
        errors.append("provider_preflight_missing")
    if "BLOCKED_MISSING_OPERATOR_SECRET" not in text:
        errors.append("provider_missing_secret_class_missing")
    if "FAILED_CERTIFICATION_IMPLEMENTATION" not in text or "preflight-wrapper.json" not in text:
        errors.append("provider_preflight_execution_failure_class_missing")
    if "if: always()" not in text or "actions/upload-artifact@v4" not in text:
        errors.append("always_cleanup_or_artifact_upload_missing")
    if (
        "check_openhands_evidence_schema.py" not in text
        or "evidence-schema-validation.json" not in text
        or 'test "$schema_status" = PASS' not in text
    ):
        errors.append("evidence_schema_validation_missing")
    if "steps.provider_preflight.outputs.ready == 'true'" not in text:
        errors.append("provider_gate_missing_before_expensive_stages")
    if "id: provider\n" not in text or "steps.provider.outputs.ready == 'true'" not in text:
        errors.append("provider_route_gate_missing_before_model_stages")
    if (
        "id: network\n" not in text
        or 'echo "ready=true" >> "$GITHUB_OUTPUT"' not in text
        or "network-startup.json" not in text
        or '"failure_class": None if created else "FAILED_INFRASTRUCTURE"' not in text
        or "steps.network.outputs.ready == 'true'" not in text
    ):
        errors.append("network_readiness_gate_missing")
    if not all(
        marker in text
        for marker in (
            "OMNIROUTE_STARTUP_FAILURE",
            "LITELLM_STARTUP_FAILURE",
            "TOOL_SERVICE_STARTUP_FAILURE",
            "OPENHANDS_AGENT_SERVER_STARTUP_FAILURE",
        )
    ):
        errors.append("startup_failure_classification_missing")
    if "steps.gateway.outputs.ready == 'true'" not in text:
        errors.append("gateway_route_gate_missing_before_worker_stages")
    if "runtime-provisioning.json" not in text or "runtime_status" not in text or 'echo "ready=true" >> "$GITHUB_OUTPUT"' not in text:
        errors.append("runtime_materialization_gate_output_missing")
    if "steps.provider_preflight.outputs.ready == 'true' && steps.preflight.outputs.ready == 'true'" not in text or re.search(
        r"(?m)^\s*if:\s*steps\.provider_preflight\.outputs\.ready\s*==\s*'true'\s*$",
        text,
    ):
        errors.append("candidate_preflight_gate_missing_before_expensive_stages")
    if (
        "mcp-cleanup.json" not in text
        or '"verified_absent"' not in text
        or 'delete_ok=false' not in text
        or 'case "$delete_code"' not in text
        or 'if [ "$delete_ok" != true ]' not in text
        or 'test "$cleanup_ok" = 1' not in text
    ):
        errors.append("cleanup_absence_readback_missing")
    if (
        "network-topology.json" not in text
        or 'docker network inspect "$network"' not in text
        or '"topology_status": "PASS" if complete else "BLOCKED"' not in text
        or '"aliases": sorted' not in text
        or "expected_aliases" not in text
        or 'test "$topology_status" = PASS' not in text
    ):
        errors.append("network_topology_assertion_missing")
    if '"zero_residue": cleanup_ok == "1"' not in text:
        errors.append("cleanup_zero_residue_not_fail_closed")
    if (
        'docker image rm "$tool_image"' not in text
        or 'rm -rf -- "$RUNNER_TEMP/aiat-openhands-workspace"' not in text
        or '"tool_image_absent": image_absent == "true"' not in text
        or '"workspace_absent": workspace_absent == "true"' not in text
        or '"profile": {' not in text
        or '"verified_by_agent_container_absence"' not in text
    ):
        errors.append("workspace_profile_or_tool_image_cleanup_missing")
    if (
        "secret_environment_entries_absent" not in text
        or "^AIAT_TOOL_SECRET=/d" not in text
        or "^OPENHANDS_MODEL_GATEWAY_API_KEY=/d" not in text
        or '"run_scoped_secret_environment_entries_absent"' not in text
    ):
        errors.append("run_scoped_secret_environment_cleanup_missing")
    if (
        "GITHUB_STEP_SUMMARY" not in text
        or "Summarize fail-closed certification result" not in text
        or "EXPECTED_FAIL_CLOSED_CERTIFICATION_BLOCK" not in text
        or "gVisor startup" not in text
        or "OmniRoute API auth boundary" not in text
        or "Model gateway route" not in text
    ):
        errors.append("fail_closed_summary_missing")
    if "docker network rm" not in text or "aiat-openhands-cert-network-${GITHUB_RUN_ID}" not in text:
        errors.append("network_cleanup_missing")
    if "vars.OPENHANDS_AGENT_PROFILE_ID" in text:
        errors.append("static_profile_uuid_input_present")
    if "secrets.AIAT_TOOL_SECRET" in text or "secrets.OPENHANDS_MODEL_GATEWAY_API_KEY" in text:
        errors.append("internal_run_scoped_secret_required_as_github_secret")
    if "OPENHANDS_MODEL_GATEWAY_URL: http://litellm:4000" not in text:
        errors.append("canonical_gateway_url_missing")
    if "check_openhands_omniroute_readiness.py" not in text or "api/monitoring/health" not in text:
        errors.append("omniroute_public_readiness_probe_missing")
    if "--publish 127.0.0.1:20129:20129" not in text or "--env API_PORT=20129" not in text:
        errors.append("omniroute_api_port_binding_missing")
    if "--env REQUIRE_API_KEY=true" not in text or "Verify OmniRoute OpenAI API authentication boundary" not in text:
        errors.append("omniroute_auth_boundary_missing")
    if "check_openhands_omniroute_auth.py" not in text or "http://127.0.0.1:20129/v1/models" not in text:
        errors.append("omniroute_auth_evidence_missing")
    if (
        "check_openhands_provider_baseline.py" not in text
        or "provider-baseline.json" not in text
        or "steps.baseline.outputs.ready == 'true'" not in text
    ):
        errors.append("provider_baseline_gate_missing")
    if "--auto-routing-output" not in text or "auto-routing.json" not in text:
        errors.append("auto_routing_evidence_missing")
    if "--host-workspace \"$RUNNER_TEMP/aiat-openhands-workspace\"" not in text or "--fixture-root \"$GITHUB_WORKSPACE/mas/scripts/fixtures/openhands-coding-task\"" not in text:
        errors.append("postrun_task_verification_missing")
    if (
        'git -C "$RUNNER_TEMP/aiat-openhands-workspace" init --quiet' not in text
        or 'git -C "$RUNNER_TEMP/aiat-openhands-workspace" commit --quiet -m "certification baseline"' not in text
    ):
        errors.append("coding_task_git_baseline_missing")
    if "--exercise-lifecycle" not in text:
        errors.append("live_lifecycle_wave_missing")
    if re.search(r"(?m)^\s*docker\s+logs\b", text):
        errors.append("raw_container_logs_retained")
    return {
        "schema_version": SCHEMA,
        "status": "PASS" if not errors else "FAILED_CERTIFICATION_IMPLEMENTATION",
        "workflow": WORKFLOW_NAME,
        "manual_only": not any(f"automatic_trigger_present:{name}" in errors for name in ("push", "pull_request", "schedule", "repository_dispatch")),
        "candidate_sha_bound": "candidate_sha_binding_missing" not in errors,
        "exact_image_pins": not any(item.startswith("exact_image_pin_missing") for item in errors),
        "run_block_shell_validation": "PASS" if not run_block_issues else "FAILED_CERTIFICATION_IMPLEMENTATION",
        "heredoc_audit": "PASS"
        if not any("heredoc" in item.lower() for item in run_block_issues)
        else "FAILED_CERTIFICATION_IMPLEMENTATION",
        "errors": errors,
        "secrets_or_payloads_retained": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflow", type=Path, default=Path(__file__).resolve().parents[2] / WORKFLOW_NAME)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        text = args.workflow.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        report = {"schema_version": SCHEMA, "status": "FAILED_CERTIFICATION_IMPLEMENTATION", "errors": [type(exc).__name__]}
    else:
        report = validate(text)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "errors": report.get("errors", [])}, sort_keys=True))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
