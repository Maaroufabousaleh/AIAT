"""Static, fail-closed validation for the manual OpenHands workflow."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

SCHEMA = "aiat.openhands-certification-workflow-validation.v1"
WORKFLOW_NAME = ".github/workflows/openhands-candidate-certification.yml"
EXPECTED_IMAGES = (
    "ghcr.io/openhands/agent-server:1.43.0-python@sha256:36f847d1dfbbbdce90052437b06a3c6e76b8a54683228182eaf73085f03fcd97",
    "ghcr.io/berriai/litellm@sha256:a50b02a6056095da29308310bb608f0509e08ddcd1d105bae9c21007d82b0e95",
    "diegosouzapw/omniroute@sha256:ceae8d9da0acf075dbf5905b61c9ae32e749112650fcf7f4434c8d96ac6d3ebb",
)


def validate(text: str) -> dict[str, Any]:
    errors: list[str] = []
    if not re.search(r"(?m)^on:\s*$", text):
        errors.append("workflow_on_key_missing")
    if not re.search(r"(?m)^\s+workflow_dispatch:\s*$", text):
        errors.append("workflow_dispatch_missing")
    for trigger in ("push:", "pull_request:", "schedule:", "repository_dispatch:"):
        if re.search(rf"(?m)^\s+{re.escape(trigger)}", text):
            errors.append(f"automatic_trigger_present:{trigger[:-1]}")
    if "actions/checkout@v4" not in text or "ref: ${{ inputs.candidate_sha }}" not in text:
        errors.append("exact_candidate_checkout_missing")
    if "actual=$(git rev-parse HEAD)" not in text or 'test "$actual" = "$EXPECTED_SHA"' not in text:
        errors.append("candidate_sha_binding_missing")
    for image in EXPECTED_IMAGES:
        if image not in text:
            errors.append(f"exact_image_pin_missing:{image.split('@', 1)[0]}")
    image_lines = [line for line in text.splitlines() if "IMAGE:" in line or "IMAGE=" in line]
    if any("latest" in line.lower() or "@sha256:" not in line for line in image_lines):
        errors.append("floating_or_unpinned_image_reference")
    if "--runtime=runsc" not in text or "sudo runsc install" not in text:
        errors.append("runsc_proof_missing")
    if "id: provider_preflight" not in text or "GROQ_API_KEY" not in text:
        errors.append("provider_preflight_missing")
    if "BLOCKED_MISSING_OPERATOR_SECRET" not in text:
        errors.append("provider_missing_secret_class_missing")
    if "if: always()" not in text or "actions/upload-artifact@v4" not in text:
        errors.append("always_cleanup_or_artifact_upload_missing")
    if "GITHUB_STEP_SUMMARY" not in text or "Summarize fail-closed certification result" not in text:
        errors.append("fail_closed_summary_missing")
    if "docker network rm" not in text or "aiat-openhands-cert-network-${GITHUB_RUN_ID}" not in text:
        errors.append("network_cleanup_missing")
    if "vars.OPENHANDS_AGENT_PROFILE_ID" in text:
        errors.append("static_profile_uuid_input_present")
    if "secrets.AIAT_TOOL_SECRET" in text or "secrets.OPENHANDS_MODEL_GATEWAY_API_KEY" in text:
        errors.append("internal_run_scoped_secret_required_as_github_secret")
    if "OPENHANDS_MODEL_GATEWAY_URL: http://litellm:4000" not in text:
        errors.append("canonical_gateway_url_missing")
    return {
        "schema_version": SCHEMA,
        "status": "PASS" if not errors else "FAILED_CERTIFICATION_IMPLEMENTATION",
        "workflow": WORKFLOW_NAME,
        "manual_only": not any(f"automatic_trigger_present:{name}" in errors for name in ("push", "pull_request", "schedule", "repository_dispatch")),
        "candidate_sha_bound": "candidate_sha_binding_missing" not in errors,
        "exact_image_pins": not any(item.startswith("exact_image_pin_missing") for item in errors),
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
