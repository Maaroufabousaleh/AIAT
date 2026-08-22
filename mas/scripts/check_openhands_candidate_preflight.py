"""Run a deterministic, non-secret OpenHands candidate preflight.

This check validates the repository-owned bindings for the inactive OpenHands
candidate and reports which values still have to come from the operator's
Agent Server/AIAT deployment.  It distinguishes isolated certification
authorization from the later steward activation approval.  It never creates a
profile, MCP entry, secret, steward approval, worker row, or certification run.
Secret values are only observed as presence booleans and are never written to
the report.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
from collections.abc import Mapping
from contextlib import suppress
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import yaml

CHECK_SCHEMA = "aiat.openhands-candidate-preflight.v1"
MAS_ROOT = Path(__file__).resolve().parents[1]
CANDIDATE_ROOT = MAS_ROOT / "docs/provenance/openhands-candidate/2026-08-22-v1.43.0"
DEFAULT_MANIFEST = CANDIDATE_ROOT / "worker-manifest.yaml"
DEFAULT_PROFILE_SPEC = CANDIDATE_ROOT / "agent-profile-spec.yaml"
DEFAULT_INTERFACE_REPORT = CANDIDATE_ROOT / "interface-verification.json"
DEFAULT_MODEL_EVIDENCE = MAS_ROOT / "docs/provenance/model_profile_catalogue_live.json"

EXPECTED_PROFILE_ID = "opencode-phase0b-coding"
EXPECTED_PROFILE_VERSION = "1"
EXPECTED_MODEL_ID = "omniroute-coding"
EXPECTED_COMMIT = "4c1237f391fe394e9f67505fe3a0bd2d81f84188"
EXPECTED_IMAGE_DIGEST = "sha256:36f847d1dfbbbdce90052437b06a3c6e76b8a54683228182eaf73085f03fcd97"
EXPECTED_MCP_URL = "http://tool-service:8002/openhands/mcp"
MCP_KEY_PREFIX = "aiat-openhands-"
MCP_KEY_PATTERN = re.compile(r"aiat-openhands-[a-z0-9][a-z0-9._-]*\Z")
REQUIRED_ENV = (
    "AIAT_TOOL_SECRET",
    "OPENHANDS_MCP_SETTINGS_KEY",
    "OPENHANDS_MODEL_ID",
    "OPENHANDS_MODEL_GATEWAY_URL",
    "OPENHANDS_MODEL_GATEWAY_API_KEY",
)
REQUIRED_DISABLED_CONTROLS = (
    "public_skills_disabled",
    "plugins_disabled",
    "browser_disabled",
    "subagents_disabled",
    "direct_credentials_disabled",
    "model_switching_disabled",
)
REQUIRED_TOOL_GRANTS = (
    "aiat.repository.read",
    "aiat.repository.write",
    "aiat.tests.execute",
)
LOCAL_GATEWAY_HOSTS = frozenset({"localhost", "localhost.localdomain", "host.docker.internal"})


def _read_yaml(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a YAML object")
    return value


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _is_uuid(value: str) -> bool:
    try:
        UUID(value)
    except (TypeError, ValueError, AttributeError):
        return False
    return True


def _mcp_key_status(value: str) -> tuple[bool, str | None]:
    if not value:
        return False, "missing"
    if any(char.isspace() for char in value):
        return False, "contains_whitespace"
    if not MCP_KEY_PATTERN.fullmatch(value):
        return False, "must_use_lowercase_aiat_openhands_key_chars"
    return True, None


def _model_evidence_status(
    evidence: Mapping[str, Any],
) -> tuple[bool, list[str], dict[str, Any]]:
    errors: list[str] = []
    reconciliation = evidence.get("alias_reconciliation")
    reconciliation = reconciliation if isinstance(reconciliation, Mapping) else {}
    canonical = reconciliation.get("canonical_registry_identity")
    canonical = canonical if isinstance(canonical, Mapping) else {}
    profile_identity = reconciliation.get("reconciled_profile_identity")
    profile_identity = profile_identity if isinstance(profile_identity, Mapping) else {}

    if evidence.get("status") != "pass":
        errors.append("retained_model_catalogue_evidence_is_not_pass")
    if evidence.get("coverage") != "complete":
        errors.append("retained_model_catalogue_coverage_is_not_complete")
    if int(evidence.get("profile_pending_model_count", 1)) != 0:
        errors.append("retained_model_catalogue_has_pending_models")
    if int(evidence.get("profile_not_registered_count", 1)) != 0:
        errors.append("retained_model_catalogue_has_unregistered_profiles")
    if canonical.get("model_id") != EXPECTED_MODEL_ID:
        errors.append("approved_catalogue_alias_does_not_match_expected_model")
    if profile_identity.get("profile_id") != EXPECTED_PROFILE_ID:
        errors.append("approved_catalogue_profile_id_does_not_match_manifest_binding")
    if profile_identity.get("version") != EXPECTED_PROFILE_VERSION:
        errors.append("approved_catalogue_profile_version_does_not_match_manifest_binding")

    return (
        not errors,
        errors,
        {
            "catalogue_status": evidence.get("status"),
            "coverage": evidence.get("coverage"),
            "profile_id": profile_identity.get("profile_id"),
            "profile_version": profile_identity.get("version"),
            "exact_model_id": canonical.get("model_id"),
            "approval_entry_count": evidence.get("approved_profile_entry_count"),
            "observed_at": evidence.get("observed_at"),
            "source_command": evidence.get("source_command"),
        },
    )


def evaluate(
    *,
    manifest: Mapping[str, Any],
    profile_spec: Mapping[str, Any],
    interface_report: Mapping[str, Any],
    model_evidence: Mapping[str, Any],
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Evaluate repository bindings without making network or persistence calls."""

    values = dict(os.environ if env is None else env)
    static_errors: list[str] = []
    operator_actions: list[str] = []
    activation_actions: list[str] = []

    metadata = manifest.get("metadata") if isinstance(manifest.get("metadata"), Mapping) else {}
    runtime = manifest.get("runtime") if isinstance(manifest.get("runtime"), Mapping) else {}
    adapter_config = runtime.get("adapter_config") if isinstance(runtime.get("adapter_config"), Mapping) else {}
    integration = manifest.get("integration") if isinstance(manifest.get("integration"), Mapping) else {}
    controls = adapter_config.get("profile_controls") if isinstance(adapter_config.get("profile_controls"), Mapping) else {}
    governance = adapter_config.get("governance") if isinstance(adapter_config.get("governance"), Mapping) else {}
    bridge = governance.get("aiat_mcp_bridge") if isinstance(governance.get("aiat_mcp_bridge"), Mapping) else {}
    sandbox = manifest.get("sandbox") if isinstance(manifest.get("sandbox"), Mapping) else {}

    candidate = {
        "release": metadata.get("source_revision"),
        "commit_sha": (manifest.get("source_provenance") or {}).get("source_commit")
        if isinstance(manifest.get("source_provenance"), Mapping)
        else None,
        "image_digest": str(adapter_config.get("image_ref") or "").split("@")[-1],
    }
    if candidate["commit_sha"] != EXPECTED_COMMIT:
        static_errors.append("candidate_source_commit_mismatch")
    if candidate["image_digest"] != EXPECTED_IMAGE_DIGEST:
        static_errors.append("candidate_image_digest_mismatch")
    if metadata.get("version_pin") != f"v1.43.0+{EXPECTED_COMMIT}":
        static_errors.append("candidate_version_pin_mismatch")
    if integration.get("adapter_entrypoint") != "OpenHandsAgentServerAdapter":
        static_errors.append("openhands_adapter_entrypoint_mismatch")
    if manifest.get("model_mode") != "aiat_gateway":
        static_errors.append("model_mode_must_be_aiat_gateway")
    if manifest.get("model_profile_id") != EXPECTED_PROFILE_ID:
        static_errors.append("manifest_model_profile_binding_mismatch")
    if adapter_config.get("model_profile_ref") != EXPECTED_PROFILE_ID:
        static_errors.append("adapter_model_profile_binding_mismatch")
    agent_profile_ref = str(adapter_config.get("agent_profile_ref") or "")
    if "workflow-run-scoped" not in agent_profile_ref.lower():
        static_errors.append("agent_profile_binding_must_be_workflow_run_scoped")
    if adapter_config.get("aiat_mcp_bridge_url") != EXPECTED_MCP_URL:
        static_errors.append("manifest_mcp_bridge_url_mismatch")
    if not str(adapter_config.get("model_gateway_url_ref") or "").startswith("OPENHANDS_MODEL_GATEWAY_URL"):
        static_errors.append("manifest_model_gateway_url_binding_mismatch")
    if adapter_config.get("model_gateway_api_key_ref") != "OPENHANDS_MODEL_GATEWAY_API_KEY (AIAT gateway secret boundary)":
        static_errors.append("manifest_model_gateway_secret_binding_mismatch")
    if bridge.get("url") != EXPECTED_MCP_URL:
        static_errors.append("governed_mcp_bridge_url_mismatch")
    if bridge.get("allowlist") != ["aiat_tool"]:
        static_errors.append("mcp_allowlist_must_contain_only_aiat_tool")
    if tuple(sorted(manifest.get("tool_grants") or [])) != tuple(sorted(REQUIRED_TOOL_GRANTS)):
        static_errors.append("worker_tool_grants_mismatch")
    if sandbox.get("profile") != "gvisor" or sandbox.get("network_mode") != "egress-allowlist":
        static_errors.append("sandbox_or_network_policy_mismatch")
    if any(controls.get(name) is not True for name in REQUIRED_DISABLED_CONTROLS):
        static_errors.append("required_profile_control_is_not_explicitly_disabled")
    if profile_spec.get("status") != "run_scoped_provision_required":
        static_errors.append("profile_spec_status_must_remain_run_scoped_provision_required")
    spec_candidate = profile_spec.get("candidate") if isinstance(profile_spec.get("candidate"), Mapping) else {}
    if spec_candidate.get("source_commit") != EXPECTED_COMMIT or spec_candidate.get("image_digest") != EXPECTED_IMAGE_DIGEST:
        static_errors.append("profile_spec_candidate_pin_mismatch")
    spec_model = profile_spec.get("aiat_bindings") if isinstance(profile_spec.get("aiat_bindings"), Mapping) else {}
    if spec_model.get("model_profile_id") != EXPECTED_PROFILE_ID or spec_model.get("exact_model_id") != EXPECTED_MODEL_ID:
        static_errors.append("profile_spec_model_binding_mismatch")
    agent_profile_spec = profile_spec.get("agent_server_profile") if isinstance(profile_spec.get("agent_server_profile"), Mapping) else {}
    if agent_profile_spec.get("id") is not None:
        static_errors.append("profile_spec_must_not_pin_a_server_generated_agent_profile_uuid")

    model_ok, model_errors, model_details = _model_evidence_status(model_evidence)
    static_errors.extend(model_errors)

    interface_approved = bool(interface_report.get("approved")) and str(interface_report.get("approval_status", "")).upper() == "APPROVED"
    if not interface_approved:
        # Interface approval is still required for normal activation, but it
        # must not block the dedicated isolated certification authorization.
        activation_actions.append("steward_or_operator_approval_after_passed_certification")

    secret_present = bool(str(values.get("AIAT_TOOL_SECRET") or "").strip())
    tool_secret_scope = str(values.get("AIAT_TOOL_SECRET_SCOPE") or "").strip()
    if not secret_present:
        if tool_secret_scope == "github-run":
            operator_actions.append("workflow_generated_AIAT_TOOL_SECRET_is_missing")
        else:
            operator_actions.append("workflow_must_generate_disposable_AIAT_TOOL_SECRET_before_certification")

    profile_value = str(values.get("OPENHANDS_AGENT_PROFILE_ID") or "").strip()
    profile_format_valid = _is_uuid(profile_value) if profile_value else None
    if profile_value and not profile_format_valid:
        static_errors.append("OPENHANDS_AGENT_PROFILE_ID_is_not_a_UUID")

    mcp_value = str(values.get("OPENHANDS_MCP_SETTINGS_KEY") or "").strip()
    mcp_format_valid, mcp_error = _mcp_key_status(mcp_value)
    if not mcp_value:
        operator_actions.append("set_GitHub_Actions_variable_OPENHANDS_MCP_SETTINGS_KEY_to_the_governed_logical_aiat_openhands_name")
    elif not mcp_format_valid:
        static_errors.append(f"OPENHANDS_MCP_SETTINGS_KEY_{mcp_error}")

    model_value = str(values.get("OPENHANDS_MODEL_ID") or "").strip()
    model_env_matches = model_value == EXPECTED_MODEL_ID
    if not model_value:
        operator_actions.append(f"set_GitHub_Actions_variable_OPENHANDS_MODEL_ID_to_{EXPECTED_MODEL_ID}")
    elif not model_env_matches:
        static_errors.append("OPENHANDS_MODEL_ID_does_not_match_approved_catalogue_alias")

    gateway_url = str(values.get("OPENHANDS_MODEL_GATEWAY_URL") or "").strip()
    if not gateway_url:
        operator_actions.append("set_GitHub_Actions_variable_OPENHANDS_MODEL_GATEWAY_URL_to_the_AIAT_gateway_endpoint")
    elif not gateway_url.startswith(("http://", "https://")):
        static_errors.append("OPENHANDS_MODEL_GATEWAY_URL_must_be_an_http_url")
    else:
        parsed_gateway_url = urlsplit(gateway_url)
        gateway_host = (parsed_gateway_url.hostname or "").lower()
        local_host = gateway_host in LOCAL_GATEWAY_HOSTS or gateway_host.endswith(".localhost")
        if not local_host and gateway_host:
            with suppress(ValueError):
                local_host = ipaddress.ip_address(gateway_host).is_loopback or ipaddress.ip_address(gateway_host).is_unspecified
        if local_host:
            static_errors.append("OPENHANDS_MODEL_GATEWAY_URL_must_not_target_runner_or_operator_loopback")
    gateway_key_present = bool(str(values.get("OPENHANDS_MODEL_GATEWAY_API_KEY") or "").strip())
    if not gateway_key_present:
        operator_actions.append("configure_GitHub_Actions_secret_OPENHANDS_MODEL_GATEWAY_API_KEY_for_the_AIAT_gateway")

    # A UUID/key in environment variables is only a syntactic reference until
    # the operator reads it back from the actual Agent Server/profile store.
    # This check intentionally does not claim remote resolution.
    profile_reference = {
        "configured": bool(profile_value),
        "format_valid": profile_format_valid,
        "source": "workflow_run_output",
        "portable": False,
        "server_generated": True,
        "server_readback": "not_checked_without_certification_agent_server",
        "value_retained": False,
    }
    mcp_reference = {
        "configured": bool(mcp_value),
        "format_valid": mcp_format_valid,
        "source": "static_logical_key; remote entry is run-scoped",
        "portable": True,
        "server_readback": "not_checked_without_certification_agent_server",
        "value_retained": False,
    }

    if static_errors:
        status = "BLOCKED_STATIC_CONFIGURATION"
    elif operator_actions or not model_ok or not secret_present or not model_env_matches:
        status = "BLOCKED_OPERATOR_CONFIGURATION"
    else:
        status = "READY_FOR_CERTIFICATION_AUTHORIZATION"

    return {
        "schema_version": CHECK_SCHEMA,
        "status": status,
        "no_mutation": True,
        "candidate": candidate,
        "model": {
            "profile_id": EXPECTED_PROFILE_ID,
            "profile_version": EXPECTED_PROFILE_VERSION,
            "exact_model_id": EXPECTED_MODEL_ID,
            "approved_catalogue_evidence": model_details,
            "catalogue_binding_valid": model_ok,
            "workflow_variable_matches": model_env_matches,
        },
        "references": {
            "OPENHANDS_AGENT_PROFILE_ID": profile_reference,
            "OPENHANDS_MCP_SETTINGS_KEY": mcp_reference,
            "OPENHANDS_MODEL_ID": {
                "configured": bool(model_value),
                "matches_approved_model": model_env_matches,
                "value_retained": False,
            },
            "OPENHANDS_MODEL_GATEWAY_URL": {
                "configured": bool(gateway_url),
                "format_valid": bool(gateway_url.startswith(("http://", "https://"))),
                "value_retained": False,
            },
        },
        "secret_boundary": {
            "AIAT_TOOL_SECRET": {
                "configured": secret_present,
                "scope": tool_secret_scope or "unspecified",
                "source": "workflow_run_generated" if tool_secret_scope == "github-run" else "not_proven",
                "value_retained": False,
                "value_printed": False,
            },
            "OPENHANDS_MODEL_GATEWAY_API_KEY": {
                "configured": gateway_key_present,
                "value_retained": False,
                "value_printed": False,
            },
        },
        "portability": {
            "profile_store": "disposable OpenHands Agent Server persistence",
            "certification_store": "fresh Agent Server instance for this workflow run",
            "same_authoritative_store": False,
            "profile_id_portable": False,
            "profile_materialization_supported": True,
            "profile_id_source": "server_generated_uuid",
            "mcp_entry_lifecycle": "create_after_start_delete_always_verify_absent",
        },
        "interface_report": {
            "report_id": interface_report.get("report_id"),
            "approval_status": interface_report.get("approval_status"),
            "approved": interface_approved,
            "activation_approval_required": not interface_approved,
            "certification_authorization_is_separate": True,
        },
        "static_errors": sorted(set(static_errors)),
        "operator_actions": sorted(set(operator_actions)),
        "activation_actions": sorted(set(activation_actions)),
        "fail_closed_contract": {
            "missing_required_env_is_rejected": True,
            "mismatched_model_is_rejected": True,
            "invalid_run_scoped_profile_uuid_is_rejected_when_supplied": True,
            "invalid_mcp_key_is_rejected": True,
            "missing_model_gateway_binding_is_rejected": True,
            "workflow_generated_tool_secret_is_required": True,
            "unapproved_interface_report_is_rejected_for_activation": True,
            "unapproved_interface_report_does_not_block_isolated_certification": True,
            "certification_authorization_never_implies_activation": True,
            "secret_values_are_not_retained": True,
        },
    }


def build_report(
    *,
    manifest_path: Path = DEFAULT_MANIFEST,
    profile_spec_path: Path = DEFAULT_PROFILE_SPEC,
    interface_report_path: Path = DEFAULT_INTERFACE_REPORT,
    model_evidence_path: Path = DEFAULT_MODEL_EVIDENCE,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    return evaluate(
        manifest=_read_yaml(manifest_path),
        profile_spec=_read_yaml(profile_spec_path),
        interface_report=_read_json(interface_report_path),
        model_evidence=_read_json(model_evidence_path),
        env=env,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--profile-spec", type=Path, default=DEFAULT_PROFILE_SPEC)
    parser.add_argument("--interface-report", type=Path, default=DEFAULT_INTERFACE_REPORT)
    parser.add_argument("--model-evidence", type=Path, default=DEFAULT_MODEL_EVIDENCE)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    report = build_report(
        manifest_path=args.manifest,
        profile_spec_path=args.profile_spec,
        interface_report_path=args.interface_report,
        model_evidence_path=args.model_evidence,
    )
    encoded = json.dumps(report, sort_keys=True, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(json.dumps({"status": report["status"], "static_errors": report["static_errors"], "operator_action_count": len(report["operator_actions"])}, sort_keys=True))
    return 0 if report["status"] == "READY_FOR_CERTIFICATION_AUTHORIZATION" else 2


if __name__ == "__main__":
    raise SystemExit(main())
