"""Safe, non-secret preflight for one deliberate OpenHands certification run."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

try:
    from check_openhands_workflow import validate as validate_workflow
except ImportError:  # pragma: no cover - package invocation fallback
    from scripts.check_openhands_workflow import validate as validate_workflow  # type: ignore

EXPECTED_MODEL = "omniroute-coding"
EXPECTED_MCP_KEY = "aiat-openhands-v1-43-0-coding"
EXPECTED_SOURCE_COMMIT = "4c1237f391fe394e9f67505fe3a0bd2d81f84188"
EXPECTED_IMAGE_DIGEST = "sha256:36f847d1dfbbbdce90052437b06a3c6e76b8a54683228182eaf73085f03fcd97"
WORKFLOW = ".github/workflows/openhands-candidate-certification.yml"
MANIFEST = "mas/docs/provenance/openhands-candidate/2026-08-22-v1.43.0/worker-manifest.yaml"
INTERFACE_REPORT = "mas/docs/provenance/openhands-candidate/2026-08-22-v1.43.0/interface-verification.json"
GATEWAY_PROVENANCE = "mas/docs/provenance/openhands-candidate/2026-08-22-v1.43.0/gateway-provenance.json"
GATEWAY_ROUTE_PROBE = "mas/scripts/check_openhands_certification_gateway.py"
PROVIDER_BASELINE_PROBE = "mas/scripts/check_openhands_provider_baseline.py"
SCHEMA = "aiat.openhands-dispatch-preflight.v1"
LOCAL_TEST_COMMAND = (
    "uv",
    "run",
    "--isolated",
    "pytest",
    "scripts/tests/test_openhands_candidate_certify.py",
    "scripts/tests/test_openhands_certification_gateway.py",
    "scripts/tests/test_openhands_model_routing.py",
    "scripts/tests/test_openhands_gateway_errors.py",
    "scripts/tests/test_openhands_gateway_provenance.py",
    "scripts/tests/test_openhands_omniroute_auth.py",
    "scripts/tests/test_openhands_omniroute_readiness.py",
    "scripts/tests/test_check_openhands_evidence_schema.py",
    "scripts/tests/test_openhands_gate_matrix.py",
    "scripts/tests/test_check_openhands_worker_comparison.py",
    "scripts/tests/test_openhands_coding_task_fixture.py",
    "scripts/tests/test_openhands_live_certify.py",
    "scripts/tests/test_openhands_offline_harness.py",
    "scripts/tests/test_check_openhands_candidate_preflight.py",
    "scripts/tests/test_check_openhands_dispatch_preflight.py",
    "scripts/tests/test_check_openhands_steward_registration.py",
    "scripts/tests/test_check_openhands_workflow.py",
    "scripts/tests/test_provision_openhands_candidate_runtime.py",
    "packages/mas-core/tests/test_openhands_agent_server_adapter.py",
    "packages/mas-core/tests/test_openhands_bridge.py",
    "apps/tool-service/tests/test_openhands_mcp.py",
    "-q",
)


def _run(command: list[str], *, cwd: Path) -> tuple[int, str]:
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)
    return result.returncode, result.stdout


def _names_from_gh(output: str) -> set[str]:
    names: set[str] = set()
    for line in output.splitlines():
        value = line.strip().split(maxsplit=1)
        if value and value[0] and (value[0].isidentifier() or value[0].replace("_", "").isalnum()):
            names.add(value[0])
    return names


def _variables_from_gh(output: str) -> dict[str, str]:
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, list):
        return {}
    result: dict[str, str] = {}
    for row in payload:
        if isinstance(row, dict) and isinstance(row.get("name"), str) and isinstance(row.get("value"), str):
            result[row["name"]] = row["value"]
    return result


def evaluate_static(
    *,
    workflow_text: str,
    manifest_text: str,
    interface_text: str = "",
    gateway_provenance_text: str = "",
    gateway_probe_text: str = "",
    provider_baseline_text: str = "",
    actual_sha: str,
    requested_sha: str | None,
    secret_names: set[str] | None,
    variable_values: dict[str, str] | None,
    local_tests_passed: bool,
    workflow_ref: str = "main",
) -> dict[str, Any]:
    workflow = validate_workflow(workflow_text)
    sha_explicit = bool(requested_sha and len(requested_sha) == 40 and requested_sha == actual_sha)
    secrets_known = secret_names is not None
    variables_known = variable_values is not None
    secret_present = "GROQ_API_KEY" in (secret_names or set()) if secrets_known else None
    variables_match = (
        variable_values is not None
        and variable_values.get("OPENHANDS_MODEL_ID") == EXPECTED_MODEL
        and variable_values.get("OPENHANDS_MCP_SETTINGS_KEY") == EXPECTED_MCP_KEY
    ) if variables_known else None
    pins_match = (
        EXPECTED_SOURCE_COMMIT in workflow_text
        and EXPECTED_SOURCE_COMMIT in manifest_text
        and (not interface_text or EXPECTED_SOURCE_COMMIT in interface_text)
        and (not gateway_provenance_text or EXPECTED_SOURCE_COMMIT in gateway_provenance_text)
        and EXPECTED_IMAGE_DIGEST in workflow_text
        and EXPECTED_IMAGE_DIGEST in manifest_text
        and (not interface_text or EXPECTED_IMAGE_DIGEST in interface_text)
        and (not gateway_provenance_text or EXPECTED_IMAGE_DIGEST in gateway_provenance_text)
        and "sha256:a50b02a6056095da29308310bb608f0509e08ddcd1d105bae9c21007d82b0e95" in workflow_text
        and (not gateway_provenance_text or "sha256:a50b02a6056095da29308310bb608f0509e08ddcd1d105bae9c21007d82b0e95" in gateway_provenance_text)
        and "sha256:ceae8d9da0acf075dbf5905b61c9ae32e749112650fcf7f4434c8d96ac6d3ebb" in workflow_text
        and (not gateway_provenance_text or "sha256:ceae8d9da0acf075dbf5905b61c9ae32e749112650fcf7f4434c8d96ac6d3ebb" in gateway_provenance_text)
    )
    gateway_probe_contract = (
        'parser.add_argument(\n        "--auto-routing-output"' in gateway_probe_text
        and "args.auto_routing_output" in gateway_probe_text
        and "_write_report(args.auto_routing_output" in gateway_probe_text
    )
    provider_baseline_contract = all(
        marker in provider_baseline_text
        for marker in (
            'parser.add_argument("--url"',
            'parser.add_argument("--output"',
            'parser.add_argument("--max-attempts"',
            'parser.add_argument("--retry-delay-seconds"',
            "args.max_attempts",
            "args.retry_delay_seconds",
            "args.output",
        )
    )
    inactive = "activation_status: inactive" in manifest_text.lower() or "certification_status: pending" in manifest_text.lower()
    checks = {
        "candidate_sha_frozen": sha_explicit,
        "workflow_manual_only": workflow.get("status") == "PASS" and workflow.get("manual_only") is True,
        "candidate_pins_match": pins_match,
        "candidate_gateway_probe_contract": gateway_probe_contract,
        "candidate_provider_baseline_contract": provider_baseline_contract,
        "github_secret_presence_known": secrets_known,
        "groq_secret_present": secret_present is True,
        "github_variables_presence_known": variables_known,
        "static_variables_match": variables_match is True,
        "no_static_profile_uuid": "vars.OPENHANDS_AGENT_PROFILE_ID" not in workflow_text,
        "no_persistent_internal_secrets": "secrets.AIAT_TOOL_SECRET" not in workflow_text and "secrets.OPENHANDS_MODEL_GATEWAY_API_KEY" not in workflow_text,
        "openhands_inactive": inactive,
        "local_deterministic_tests": local_tests_passed,
    }
    ready = all(checks.values())
    blocking_reasons: list[str] = []
    if not checks["candidate_sha_frozen"]:
        blocking_reasons.append("CANDIDATE_SHA_MISMATCH")
    if not checks["workflow_manual_only"]:
        blocking_reasons.append("WORKFLOW_STATIC_VALIDATION_FAILED")
    if not checks["candidate_pins_match"]:
        blocking_reasons.append("CANDIDATE_PROVENANCE_MISMATCH")
    if not checks["candidate_gateway_probe_contract"] or not checks["candidate_provider_baseline_contract"]:
        blocking_reasons.append("CANDIDATE_HELPER_CONTRACT_MISMATCH")
    if not checks["github_secret_presence_known"] or not checks["github_variables_presence_known"]:
        blocking_reasons.append("GITHUB_CONFIGURATION_PRESENCE_UNKNOWN")
    elif not checks["groq_secret_present"] or not checks["static_variables_match"]:
        blocking_reasons.append("GITHUB_CONFIGURATION_INCOMPLETE")
    if not checks["no_static_profile_uuid"] or not checks["no_persistent_internal_secrets"]:
        blocking_reasons.append("PERSISTENT_INTERNAL_RUNTIME_INPUT_PRESENT")
    if not checks["openhands_inactive"]:
        blocking_reasons.append("OPENHANDS_NOT_INACTIVE")
    if not checks["local_deterministic_tests"]:
        blocking_reasons.append("LOCAL_DETERMINISTIC_VALIDATION_FAILED")
    implementation_blockers = {
        "WORKFLOW_STATIC_VALIDATION_FAILED",
        "CANDIDATE_PROVENANCE_MISMATCH",
        "CANDIDATE_HELPER_CONTRACT_MISMATCH",
        "LOCAL_DETERMINISTIC_VALIDATION_FAILED",
    }
    status = (
        "PASS"
        if ready
        else "FAILED_CERTIFICATION_IMPLEMENTATION"
        if any(reason in implementation_blockers for reason in blocking_reasons)
        else "BLOCKED_OPERATOR_CONFIGURATION"
    )
    dispatch_ref = workflow_ref.strip() or "main"
    return {
        "schema_version": SCHEMA,
        "status": status,
        "ready_to_dispatch": ready,
        "candidate_sha": actual_sha,
        "requested_candidate_sha": requested_sha,
        "requested_sha_supplied": bool(requested_sha),
        "checks": checks,
        "blocking_reasons": blocking_reasons,
        "github_secret_presence": "YES" if secret_present is True else "NO" if secret_present is False else "UNKNOWN",
        "static_variables": {
            "OPENHANDS_MODEL_ID": EXPECTED_MODEL if variables_match is True else "UNKNOWN",
            "OPENHANDS_MCP_SETTINGS_KEY": EXPECTED_MCP_KEY if variables_match is True else "UNKNOWN",
        },
        "candidate_helper_contracts": {
            "gateway_route_probe": "PASS" if gateway_probe_contract else "BLOCKED",
            "provider_baseline_probe": "PASS" if provider_baseline_contract else "BLOCKED",
            "workflow_arguments_are_supported_by_checked_out_helpers": (
                gateway_probe_contract and provider_baseline_contract
            ),
        },
        "run_scoped_values": [
            "OPENHANDS_AGENT_PROFILE_ID",
            "OPENHANDS_CERT_RUN_ID",
            "OPENHANDS_PROJECT_ID",
            "AIAT_TOOL_SECRET",
            "OPENHANDS_SESSION_API_KEY",
            "OPENHANDS_MODEL_GATEWAY_API_KEY",
        ],
        # The workflow definition is selected by a branch/tag ref; the exact
        # candidate SHA is carried separately as the required dispatch input
        # and is verified again after checkout. Passing a raw SHA as --ref is
        # not portable across GitHub workflow-dispatch implementations.
        "workflow_ref": dispatch_ref,
        "dispatch_command": (
            "gh workflow run openhands-candidate-certification.yml "
            f"--ref {dispatch_ref} -f candidate_sha={actual_sha}"
        ),
        "secrets_or_payloads_retained": False,
    }


def preflight(repo: Path, requested_sha: str | None, repo_slug: str | None, skip_tests: bool) -> dict[str, Any]:
    _, actual_sha_output = _run(["git", "rev-parse", "HEAD"], cwd=repo)
    actual_sha = actual_sha_output.strip()
    workflow_text = (repo / WORKFLOW).read_text(encoding="utf-8")
    manifest_text = (repo / MANIFEST).read_text(encoding="utf-8")
    interface_text = (repo / INTERFACE_REPORT).read_text(encoding="utf-8")
    gateway_provenance_text = (repo / GATEWAY_PROVENANCE).read_text(encoding="utf-8")
    try:
        gateway_probe_text = (repo / GATEWAY_ROUTE_PROBE).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        gateway_probe_text = ""
    try:
        provider_baseline_text = (repo / PROVIDER_BASELINE_PROBE).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        provider_baseline_text = ""
    secret_names: set[str] | None = None
    variable_values: dict[str, str] | None = None
    if repo_slug:
        code, output = _run(["gh", "secret", "list", "--repo", repo_slug], cwd=repo)
        if code == 0:
            secret_names = _names_from_gh(output)
        code, output = _run(["gh", "variable", "list", "--repo", repo_slug, "--json", "name,value"], cwd=repo)
        if code == 0:
            variable_values = _variables_from_gh(output)
    if skip_tests:
        local_tests_passed = False
        local_validation = {
            "status": "SKIPPED",
            "runner": "uv run --isolated pytest",
            "exit_code": None,
            "output_retained": False,
        }
    else:
        code, _ = _run(list(LOCAL_TEST_COMMAND), cwd=repo / "mas")
        local_tests_passed = code == 0
        local_validation = {
            "status": "PASS" if local_tests_passed else "BLOCKED",
            "runner": "uv run --isolated pytest",
            "exit_code": code,
            "output_retained": False,
        }
    code, branch = _run(["git", "branch", "--show-current"], cwd=repo)
    workflow_ref = branch.strip() if code == 0 and branch.strip() else "main"
    report = evaluate_static(
        workflow_text=workflow_text,
        manifest_text=manifest_text,
        interface_text=interface_text,
        gateway_provenance_text=gateway_provenance_text,
        gateway_probe_text=gateway_probe_text,
        provider_baseline_text=provider_baseline_text,
        actual_sha=actual_sha,
        requested_sha=requested_sha,
        secret_names=secret_names,
        variable_values=variable_values,
        local_tests_passed=local_tests_passed,
        workflow_ref=workflow_ref,
    )
    report["local_validation"] = local_validation
    report["branch"] = branch.strip() if code == 0 else None
    code, status = _run(["git", "status", "--porcelain"], cwd=repo)
    report["worktree_status"] = "CLEAN" if code == 0 and not status.strip() else "UNDERSTOOD_DIRTY"
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--github-repo", help="owner/repo for read-only gh secret/variable presence checks")
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    report = preflight(args.repo.resolve(), args.candidate_sha, args.github_repo, args.skip_tests)
    serialized = json.dumps(report, sort_keys=True, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding="utf-8")
    print(
        json.dumps(
            {
                "status": report["status"],
                "ready_to_dispatch": report["ready_to_dispatch"],
                "candidate_sha": report["candidate_sha"],
                "groq_secret_present": report["github_secret_presence"],
                "blocking_reasons": report.get("blocking_reasons", []),
            },
            sort_keys=True,
        )
    )
    return 0 if report["ready_to_dispatch"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
