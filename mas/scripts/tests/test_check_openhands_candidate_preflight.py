"""Tests for the non-secret OpenHands candidate preflight."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "check_openhands_candidate_preflight.py"
SPEC = importlib.util.spec_from_file_location("check_openhands_candidate_preflight", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _inputs() -> tuple[dict, dict, dict, dict]:
    return (
        MODULE._read_yaml(MODULE.DEFAULT_MANIFEST),
        MODULE._read_yaml(MODULE.DEFAULT_PROFILE_SPEC),
        MODULE._read_json(MODULE.DEFAULT_INTERFACE_REPORT),
        MODULE._read_json(MODULE.DEFAULT_MODEL_EVIDENCE),
    )


def test_current_candidate_fails_closed_without_operator_values() -> None:
    manifest, profile_spec, interface_report, model_evidence = _inputs()
    report = MODULE.evaluate(
        manifest=manifest,
        profile_spec=profile_spec,
        interface_report=interface_report,
        model_evidence=model_evidence,
        env={},
    )

    assert report["status"] == "BLOCKED_OPERATOR_CONFIGURATION"
    assert report["model"]["catalogue_binding_valid"] is True
    assert report["references"]["OPENHANDS_AGENT_PROFILE_ID"]["configured"] is False
    assert report["references"]["OPENHANDS_MCP_SETTINGS_KEY"]["configured"] is False
    assert report["references"]["OPENHANDS_MODEL_ID"]["configured"] is False
    assert report["references"]["OPENHANDS_MODEL_GATEWAY_URL"]["configured"] is False
    assert report["secret_boundary"]["AIAT_TOOL_SECRET"]["configured"] is False
    assert report["interface_report"]["approved"] is False
    assert report["fail_closed_contract"]["missing_required_env_is_rejected"] is True


def test_mismatched_references_are_rejected_without_retaining_values() -> None:
    manifest, profile_spec, interface_report, model_evidence = _inputs()
    secret = "operator-secret-must-never-appear-in-report"
    report = MODULE.evaluate(
        manifest=manifest,
        profile_spec=profile_spec,
        interface_report=interface_report,
        model_evidence=model_evidence,
        env={
            "AIAT_TOOL_SECRET": secret,
            "OPENHANDS_AGENT_PROFILE_ID": "not-a-uuid",
            "OPENHANDS_MCP_SETTINGS_KEY": "wrong-key",
            "OPENHANDS_MODEL_ID": "auto",
            "OPENHANDS_MODEL_GATEWAY_URL": "not-a-url",
            "OPENHANDS_MODEL_GATEWAY_API_KEY": "gateway-secret",
        },
    )

    assert report["status"] == "BLOCKED_STATIC_CONFIGURATION"
    assert any("UUID" in error for error in report["static_errors"])
    assert any("MCP_SETTINGS_KEY" in error for error in report["static_errors"])
    assert any("MODEL_ID" in error for error in report["static_errors"])
    assert secret not in json.dumps(report, sort_keys=True)
    assert report["secret_boundary"]["AIAT_TOOL_SECRET"]["value_retained"] is False


def test_valid_reference_shapes_are_ready_for_isolated_certification_but_not_activation() -> None:
    manifest, profile_spec, interface_report, model_evidence = _inputs()
    report = MODULE.evaluate(
        manifest=manifest,
        profile_spec=profile_spec,
        interface_report=interface_report,
        model_evidence=model_evidence,
        env={
            "AIAT_TOOL_SECRET": "operator-secret",
            "OPENHANDS_AGENT_PROFILE_ID": "5e8f2b8a-9d9c-4a7f-9c82-14d8ccf9dd31",
            "OPENHANDS_MCP_SETTINGS_KEY": MODULE.EXPECTED_MCP_KEY,
            "OPENHANDS_MODEL_ID": "omniroute-coding",
            "OPENHANDS_MODEL_GATEWAY_URL": "http://litellm:4000",
            "OPENHANDS_MODEL_GATEWAY_API_KEY": "gateway-secret",
            "OPENHANDS_MODEL_GATEWAY_API_KEY_SCOPE": "github-run",
        },
    )

    assert report["status"] == "READY_FOR_CERTIFICATION_AUTHORIZATION"
    assert report["static_errors"] == []
    assert report["references"]["OPENHANDS_AGENT_PROFILE_ID"]["format_valid"] is True
    assert report["references"]["OPENHANDS_MCP_SETTINGS_KEY"]["format_valid"] is True
    assert report["references"]["OPENHANDS_AGENT_PROFILE_ID"]["server_readback"] == "not_checked_without_certification_agent_server"
    assert report["references"]["OPENHANDS_AGENT_PROFILE_ID"]["source"] == "workflow_run_output"
    assert report["portability"]["profile_id_portable"] is False
    assert report["interface_report"]["approved"] is False
    assert report["interface_report"]["certification_authorization_is_separate"] is True
    assert report["activation_actions"] == ["steward_or_operator_approval_after_passed_certification"]


def test_run_scoped_profile_uuid_is_not_a_static_ci_prerequisite() -> None:
    manifest, profile_spec, interface_report, model_evidence = _inputs()
    report = MODULE.evaluate(
        manifest=manifest,
        profile_spec=profile_spec,
        interface_report=interface_report,
        model_evidence=model_evidence,
        env={
            "AIAT_TOOL_SECRET": "operator-secret",
            "OPENHANDS_MCP_SETTINGS_KEY": MODULE.EXPECTED_MCP_KEY,
            "OPENHANDS_MODEL_ID": "omniroute-coding",
            "OPENHANDS_MODEL_GATEWAY_URL": "http://litellm:4000",
            "OPENHANDS_MODEL_GATEWAY_API_KEY": "gateway-secret",
            "OPENHANDS_MODEL_GATEWAY_API_KEY_SCOPE": "github-run",
        },
    )
    assert report["status"] == "READY_FOR_CERTIFICATION_AUTHORIZATION"
    assert report["references"]["OPENHANDS_AGENT_PROFILE_ID"]["configured"] is False
    assert report["references"]["OPENHANDS_AGENT_PROFILE_ID"]["source"] == "workflow_run_output"


def test_workflow_generated_tool_secret_is_recorded_without_retaining_scope_value() -> None:
    manifest, profile_spec, interface_report, model_evidence = _inputs()
    report = MODULE.evaluate(
        manifest=manifest,
        profile_spec=profile_spec,
        interface_report=interface_report,
        model_evidence=model_evidence,
        env={
            "AIAT_TOOL_SECRET": "run-secret-must-never-appear-in-report",
            "AIAT_TOOL_SECRET_SCOPE": "github-run",
            "OPENHANDS_MCP_SETTINGS_KEY": MODULE.EXPECTED_MCP_KEY,
            "OPENHANDS_MODEL_ID": "omniroute-coding",
            "OPENHANDS_MODEL_GATEWAY_URL": "http://litellm:4000",
            "OPENHANDS_MODEL_GATEWAY_API_KEY": "gateway-secret",
            "OPENHANDS_MODEL_GATEWAY_API_KEY_SCOPE": "github-run",
        },
    )

    assert report["status"] == "READY_FOR_CERTIFICATION_AUTHORIZATION"
    boundary = report["secret_boundary"]["AIAT_TOOL_SECRET"]
    assert boundary["scope"] == "github-run"
    assert boundary["source"] == "workflow_run_generated"
    assert boundary["value_retained"] is False
    assert "run-secret-must-never-appear-in-report" not in json.dumps(report, sort_keys=True)


def test_gateway_loopback_is_rejected_as_a_nonportable_ci_endpoint() -> None:
    manifest, profile_spec, interface_report, model_evidence = _inputs()
    report = MODULE.evaluate(
        manifest=manifest,
        profile_spec=profile_spec,
        interface_report=interface_report,
        model_evidence=model_evidence,
        env={
            "AIAT_TOOL_SECRET": "run-secret",
            "OPENHANDS_MCP_SETTINGS_KEY": MODULE.EXPECTED_MCP_KEY,
            "OPENHANDS_MODEL_ID": "omniroute-coding",
            "OPENHANDS_MODEL_GATEWAY_URL": "http://127.0.0.1:4000",
            "OPENHANDS_MODEL_GATEWAY_API_KEY": "gateway-secret",
        },
    )

    assert "OPENHANDS_MODEL_GATEWAY_URL_must_equal_http://litellm:4000" in report["static_errors"]
    assert report["status"] == "BLOCKED_STATIC_CONFIGURATION"


def test_v143_wire_model_binding_is_required_in_current_manifest() -> None:
    manifest, profile_spec, interface_report, model_evidence = _inputs()
    manifest["runtime"]["adapter_config"].pop("openhands_wire_model_id", None)
    report = MODULE.evaluate(
        manifest=manifest,
        profile_spec=profile_spec,
        interface_report=interface_report,
        model_evidence=model_evidence,
        env={},
    )

    assert "manifest_openhands_wire_model_binding_mismatch" in report["static_errors"]
    assert report["status"] == "BLOCKED_STATIC_CONFIGURATION"


def test_generic_http_transport_is_rejected_for_openhands_candidate() -> None:
    manifest, profile_spec, interface_report, model_evidence = _inputs()
    manifest["runtime"]["transport"] = "http"
    report = MODULE.evaluate(
        manifest=manifest,
        profile_spec=profile_spec,
        interface_report=interface_report,
        model_evidence=model_evidence,
        env={},
    )
    assert "openhands_transport_binding_mismatch" in report["static_errors"]
    assert report["status"] == "BLOCKED_STATIC_CONFIGURATION"


def test_skill_and_plugin_controls_require_the_pinned_runtime_boundary() -> None:
    manifest, profile_spec, interface_report, model_evidence = _inputs()
    manifest["runtime"]["adapter_config"]["profile_controls"]["public_skills_control"] = "allow_all"
    report = MODULE.evaluate(
        manifest=manifest,
        profile_spec=profile_spec,
        interface_report=interface_report,
        model_evidence=model_evidence,
        env={},
    )
    assert "public_skill_deny_list_control_missing" in report["static_errors"]
    assert report["status"] == "BLOCKED_STATIC_CONFIGURATION"
