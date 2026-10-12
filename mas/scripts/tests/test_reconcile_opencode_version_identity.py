"""Regression checks for exact OpenCode active/candidate identity reporting."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

SCRIPT = Path(__file__).resolve().parents[1] / "reconcile_opencode_version_identity.py"


def _run_report() -> tuple[dict[str, Any], str]:
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout), result.stdout


def _all_keys(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [key for key, item in value.items() for key in [key, *_all_keys(item)]]
    if isinstance(value, list):
        return [key for item in value for key in _all_keys(item)]
    return []


def test_current_reconciliation_distinguishes_active_and_retained_candidate() -> None:
    report, _ = _run_report()

    assert report["schema_version"] == "aiat.opencode-version-reconciliation.v1"
    assert report["decision"] == "BLOCKED_IDENTITY_COMPATIBILITY_AND_GOVERNANCE"
    assert report["activation_changed"] is False
    assert report["worker_identity_mutated"] is False
    assert report["external_provider_called"] is False

    for worker in report["workers"]:
        assert worker["source"]["version_pin"] == "1.17.13"
        assert worker["source"]["exact_release"] == "1.17.13"
        assert worker["runtime"]["interface_report_id"] == "opencode-phase0b-1.17.13"

    candidate = report["candidate"]
    assert candidate["version"] == "1.18.21"
    assert candidate["source"]["commit"] == "826d9ad46a22bef0294998e08daa3c4904fea28f"
    assert candidate["source"]["snapshot_sha256_verified"] is True
    assert candidate["source"]["archive_sha256_verified"] is True
    assert candidate["oci_image"]["digest_matches_reference"] is True
    assert candidate["certification"]["status"] == "blocked"

    assert report["runtime"]["build_version"] == "1.17.13"
    assert report["runtime"]["dockerfile_default_version"] == "1.17.13"
    assert report["runtime"]["sandbox_runtime"] == "runsc"
    assert report["runtime"]["runtime_catalog"] == {
        "runtime_id": "external",
        "supported_transports": ["process", "http", "oci", "mcp", "opencode", "wasmtime"],
        "supported_isolation_modes": ["wrapper", "fork", "opencode"],
        "catalogue_ref": "mas/packages/mas-core/mas_core/worker_registry/runtime_catalog.py:RUNTIME_CATALOG.external",
        "version_pin_policy": "catalogue declares no runtime version; worker manifest and immutable image provenance carry exact version",
    }
    assert report["adapter"]["approved_release"] == "1.17.13"
    assert report["adapter"]["candidate_report_present"] is False
    assert report["adapter"]["compatible_for_promotion"] is False
    conformance = report["adapter"]["retained_candidate_conformance"]
    assert conformance["report_ref"].endswith(
        "opencode_adapter_conformance_1.17.13_vs_1.18.21_2026-10-12.json"
    )
    assert conformance["report_sha256"] == "53d23d1b7d4df17c7040b9805f075a2cbfcdcf36a29686770fd51addb3fbea34"
    assert conformance["candidate_identity_matches_retained_source_and_image"] is True
    assert conformance["non_model_lifecycle_operations_passed"] is True
    assert conformance["schema_matches_approved_contract"] is False
    assert conformance["prompt_async_status"] == "NOT_RUN_BY_DESIGN"
    assert conformance["tool_mediation_status"] == "NOT_RUN_BY_DESIGN"
    assert conformance["provider_request_count"] == 0
    assert conformance["promotion_compatible"] is False
    assert report["checks"]["candidate_non_model_adapter_lifecycle"] == "PASS"
    assert report["persisted_candidate"]["identity_observed"] is False
    assert report["persisted_candidate"]["steward_candidate_status"] == "not_observed_persisted"


def test_reconciliation_json_is_stable_and_contains_no_secret_fields() -> None:
    report_one, rendered_one = _run_report()
    report_two, rendered_two = _run_report()

    assert rendered_one == rendered_two
    forbidden_key_parts = ("password", "secret", "api_key", "token_value", "credential_value")
    assert not any(
        any(part in key.lower() for part in forbidden_key_parts)
        for key in _all_keys(report_one)
    )
