"""Emit a read-only, exact-identity reconciliation for OpenCode workers.

This report compares the active 1.17.13 worker/runtime declarations with the
retained 1.18.21 candidate. It never mutates worker, steward, or runtime state.
Historical API observations are labeled as retained observations rather than
presenting them as a live read.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import lzma
import re
import sys
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_VERSION = "aiat.opencode-version-reconciliation.v1"
R4_DIR = Path("mas/docs/provenance/opencode-candidate/2026-10-02-v1.18.21-r4")
RETAINED_REPORT = Path("mas/docs/provenance/opencode_version_reconciliation_2026-10-12.json")
ADAPTER_CONFORMANCE_REPORT = Path(
    "mas/docs/provenance/opencode_adapter_conformance_1.17.13_vs_1.18.21_2026-10-12.json"
)
WORKER_IDS = ("coding_worker", "tester")


def _read_json(root: Path, relative: str | Path) -> dict[str, Any]:
    value = json.loads((root / relative).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object in {relative}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_xz_payload(path: Path) -> str:
    digest = hashlib.sha256()
    with lzma.open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_worker(root: Path, worker_id: str) -> dict[str, Any]:
    relative = Path("mas/workers") / f"{worker_id}.yaml"
    source = yaml.safe_load((root / relative).read_text(encoding="utf-8"))
    metadata = source.get("metadata") or {}
    runtime = source.get("runtime") or {}
    adapter_config = runtime.get("adapter_config") or {}
    integration = source.get("integration") or {}
    provenance = source.get("source_provenance") or {}
    return {
        "worker_id": worker_id,
        "manifest_ref": relative.as_posix(),
        "source": {
            "repository": metadata.get("source_repo"),
            "revision": metadata.get("source_revision"),
            "version_pin": metadata.get("version_pin"),
            "exact_release": provenance.get("exact_release"),
        },
        "runtime": {
            "transport": runtime.get("transport"),
            "runtime_tier": source.get("runtime_tier"),
            "isolation_mode": integration.get("isolation_mode"),
            "adapter_entrypoint": adapter_config.get("entrypoint")
            or integration.get("adapter_entrypoint"),
            "adapter_version": integration.get("certified_adapter_version"),
            "interface_report_id": (adapter_config.get("interface_verification") or {}).get(
                "report_id"
            ),
            "model_mode": source.get("model_mode"),
            "sandbox_profile": (source.get("sandbox") or {}).get("profile"),
        },
        "governance": {
            "evaluation_status": metadata.get("evaluation_status"),
            "certification_status": source.get("certification_status"),
            "security_scan_status": provenance.get("security_scan_status"),
            "security_scan_evidence_ref": provenance.get("security_scan_evidence_ref"),
        },
    }


def _compose_runtime(root: Path) -> dict[str, Any]:
    compose_ref = Path("mas/infra/compose/docker-compose.yml")
    compose = yaml.safe_load((root / compose_ref).read_text(encoding="utf-8"))
    runtime = compose["services"]["opencode-runtime"]
    args = runtime.get("build", {}).get("args", {})
    dockerfile_ref = Path("mas/infra/docker/Dockerfile.opencode")
    dockerfile = (root / dockerfile_ref).read_text(encoding="utf-8")
    version_match = re.search(r"^ARG OPENCODE_VERSION=(\S+)$", dockerfile, re.MULTILINE)
    tarball_match = re.search(r"^ARG OPENCODE_TARBALL_SHA512=(\S+)$", dockerfile, re.MULTILINE)
    if not version_match or not tarball_match:
        raise ValueError("OpenCode Dockerfile must pin both version and tarball checksum")
    return {
        "compose_ref": f"{compose_ref.as_posix()}:services.opencode-runtime",
        "image_reference": runtime.get("image"),
        "build_version": str(args.get("OPENCODE_VERSION", "")),
        "sandbox_runtime": runtime.get("runtime"),
        "network_names": sorted((runtime.get("networks") or {}).keys()),
        "read_only_root": bool(runtime.get("read_only")),
        "dockerfile_ref": dockerfile_ref.as_posix(),
        "dockerfile_package": "opencode-ai",
        "dockerfile_default_version": version_match.group(1),
        "dockerfile_tarball_sha512_base64": tarball_match.group(1),
        "project_config_disabled": "OPENCODE_DISABLE_PROJECT_CONFIG=true" in dockerfile,
    }


def _external_runtime_catalog_entry(source: str) -> dict[str, Any]:
    """Read the external runtime contract without importing the application."""

    tree = ast.parse(source)
    catalog_node: ast.Dict | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "RUNTIME_CATALOG":
            catalog_node = node.value if isinstance(node.value, ast.Dict) else None
            break
    if catalog_node is None:
        raise ValueError("runtime catalogue has no literal RUNTIME_CATALOG mapping")
    for key, value in zip(catalog_node.keys, catalog_node.values, strict=False):
        if not isinstance(key, ast.Constant) or key.value != "external":
            continue
        if not isinstance(value, ast.Call) or not value.args:
            raise ValueError("external runtime catalogue entry is not a RuntimeDefinition call")
        if not isinstance(value.args[0], ast.Constant) or value.args[0].value != "external":
            raise ValueError("external runtime catalogue ID does not match its key")
        keywords = {item.arg: item.value for item in value.keywords if item.arg}
        transports = ast.literal_eval(keywords.get("supported_transports", ast.Tuple(elts=[], ctx=ast.Load())))
        isolation_modes = ast.literal_eval(
            keywords.get("supported_isolation_modes", ast.Tuple(elts=[], ctx=ast.Load()))
        )
        return {
            "runtime_id": "external",
            "supported_transports": list(transports),
            "supported_isolation_modes": list(isolation_modes),
            "catalogue_ref": "mas/packages/mas-core/mas_core/worker_registry/runtime_catalog.py:RUNTIME_CATALOG.external",
            "version_pin_policy": "catalogue declares no runtime version; worker manifest and immutable image provenance carry exact version",
        }
    raise ValueError("runtime catalogue has no external entry")


def build_report(root: Path = REPO_ROOT) -> dict[str, Any]:
    workers = [_load_worker(root, worker_id) for worker_id in WORKER_IDS]
    source = _read_json(root, R4_DIR / "source-manifest.json")
    candidate = _read_json(root, R4_DIR / "candidate-certification.json")
    candidate_probe = _read_json(root, RETAINED_REPORT)
    adapter_conformance = _read_json(root, ADAPTER_CONFORMANCE_REPORT)
    approved_interface = _read_json(
        root,
        "mas/packages/mas-core/mas_core/worker_registry/fixtures/opencode-phase0b-1.17.13.json",
    )
    runtime_catalog_source = (root / "mas/packages/mas-core/mas_core/worker_registry/runtime_catalog.py").read_text(
        encoding="utf-8"
    )
    runtime = _compose_runtime(root)

    source_archive = root / R4_DIR / str(source["snapshot_path"])
    snapshot_file_digest = _sha256(source_archive)
    uncompressed_archive_digest = _sha256_xz_payload(source_archive)
    candidate_image = str(candidate.get("candidate_image_ref") or "")
    image_digest = str(candidate.get("candidate_image_digest") or "")
    cert_status = str(candidate.get("status") or "unknown")
    blockers = list(candidate.get("blockers") or [])

    retained_adapter = candidate_probe.get("adapter_compatibility") or {}
    retained_probe = retained_adapter.get("candidate_1_18_21_live_interface_probe") or {}
    retained_worker_observation = candidate_probe.get("live_read_only_worker_and_project_observation") or {}
    retained_worker = retained_worker_observation.get("worker") or {}
    persisted = candidate_probe.get("candidate_1_18_21", {}).get("worker_manifest_registration") or {}
    human_security_review = candidate_probe.get("security_evidence_identity") or {}

    manifest_versions_match = all(
        worker["source"]["version_pin"] == source.get("release")
        for worker in workers
    )
    runtime_versions_match_candidate = (
        runtime["build_version"] == source.get("release")
        and runtime["dockerfile_default_version"] == source.get("release")
    )
    adapter_report_match_candidate = (
        approved_interface.get("release") == source.get("release")
        and retained_probe.get("schema_matches_approved_1_17_13_report") is True
        and bool(retained_adapter.get("candidate_1_18_21_interface_report_present"))
    )
    exact_source_match = (
        source.get("release") == candidate.get("candidate_version")
        and source.get("commit") == candidate.get("candidate_commit")
        and snapshot_file_digest == source.get("snapshot_sha256")
        and uncompressed_archive_digest == source.get("archive_sha256")
    )
    image_identity_match = bool(
        re.search(r"@sha256:" + re.escape(image_digest) + r"$", candidate_image)
        and len(image_digest) == 64
    )
    governance_ready = cert_status == "passed" and not blockers and not human_security_review.get(
        "human_or_security_disposition_required", True
    )
    persisted_candidate_present = bool(persisted.get("persisted_candidate_identity_observed"))

    if not exact_source_match or not image_identity_match:
        decision = "FAIL_CANDIDATE_PROVENANCE_MISMATCH"
    elif not adapter_report_match_candidate or not governance_ready or not persisted_candidate_present:
        decision = "BLOCKED_IDENTITY_COMPATIBILITY_AND_GOVERNANCE"
    elif not manifest_versions_match or not runtime_versions_match_candidate:
        decision = "BLOCKED_VERSION_ALIGNMENT_REQUIRED"
    else:
        decision = "READY_FOR_GOVERNED_CANDIDATE_REVIEW"

    runtime_catalog = _external_runtime_catalog_entry(runtime_catalog_source)
    runtime_catalog_supports_opencode = "opencode" in runtime_catalog["supported_transports"]
    adapter_conformance_candidate = adapter_conformance.get("candidate") or {}
    adapter_conformance_contract = adapter_conformance.get("adapter_contract") or {}
    adapter_conformance_operations = adapter_conformance.get("operations") or {}
    adapter_conformance_candidate_identity_matches = (
        adapter_conformance_candidate.get("runtime_version_command_output")
        == source.get("release")
        and adapter_conformance_candidate.get("expected_reference") == candidate_image
        and adapter_conformance_candidate.get("image_id") == candidate_image.split("@")[-1]
        and adapter_conformance_candidate.get("candidate_source", {}).get("source_commit")
        == source.get("commit")
    )
    adapter_conformance_non_model_lifecycle_passed = all(
        adapter_conformance_operations.get(name, {}).get("status") == "PASS"
        for name in (
            "authenticated_health",
            "session_create",
            "session_read",
            "event_stream",
            "session_abort",
            "session_cleanup",
        )
    )
    candidate_scanners = [
        {
            "name": scanner.get("name"),
            "status": scanner.get("status"),
            "finding_count": scanner.get("finding_count"),
            "scanner_error_count": scanner.get("scanner_error_count"),
            "raw_json_path": scanner.get("raw_json_path"),
            "raw_json_sha256": scanner.get("raw_json_sha256"),
        }
        for scanner in candidate.get("scanners", [])
    ]

    return {
        "schema_version": SCHEMA_VERSION,
        "decision": decision,
        "activation_changed": False,
        "worker_identity_mutated": False,
        "external_provider_called": False,
        "workers": workers,
        "candidate": {
            "version": candidate.get("candidate_version"),
            "tag": candidate.get("candidate_tag"),
            "source": {
                "repository": source.get("repository"),
                "tag": source.get("tag"),
                "release": source.get("release"),
                "commit": source.get("commit"),
                "snapshot_ref": (R4_DIR / str(source.get("snapshot_path"))).as_posix(),
                "snapshot_sha256": source.get("snapshot_sha256"),
                "snapshot_sha256_verified": snapshot_file_digest == source.get("snapshot_sha256"),
                "archive_sha256": source.get("archive_sha256"),
                "archive_sha256_verified": uncompressed_archive_digest == source.get("archive_sha256"),
            },
            "oci_image": {
                "reference": candidate_image,
                "digest": f"sha256:{image_digest}" if image_digest else None,
                "digest_matches_reference": image_identity_match,
                "local_image_version_crosscheck": (
                    candidate_probe.get("candidate_1_18_21", {}).get("upstream_oci_image", {}).get(
                        "local_observation", {}
                    )
                ),
            },
            "certification": {
                "report_ref": (R4_DIR / "candidate-certification.json").as_posix(),
                "status": cert_status,
                "blockers": blockers,
                "scanner_errors": candidate.get("scanner_errors"),
                "security_disposition_complete": not bool(human_security_review.get(
                    "human_or_security_disposition_required", True
                )),
                "scanner_results": candidate_scanners,
            },
        },
        "runtime": {
            **runtime,
            "active_compose_runtime_version_matches_candidate": runtime["build_version"]
            == source.get("release"),
            "runtime_catalog_opencode_transport_supported": runtime_catalog_supports_opencode,
            "runtime_catalog": runtime_catalog,
            "local_container_observation": candidate_probe.get("aiat_runtime", {}).get(
                "observed_local_container"
            ),
        },
        "adapter": {
            "entrypoint": "OpenCodeAdapter",
            "version": "1.0.0",
            "source_ref": retained_adapter.get("adapter_source_ref"),
            "approved_report_ref": retained_adapter.get("approved_report_ref"),
            "approved_report_id": approved_interface.get("report_id"),
            "approved_release": approved_interface.get("release"),
            "approved_openapi_sha256": approved_interface.get("openapi_sha256"),
            "candidate_report_present": retained_adapter.get("candidate_1_18_21_interface_report_present"),
            "candidate_probe": retained_probe,
            "candidate_compatibility": retained_adapter.get("deployment_compatibility"),
            "candidate_compatibility_reason": retained_adapter.get("deployment_compatibility_reason"),
            "compatible_for_promotion": adapter_report_match_candidate,
            "retained_candidate_conformance": {
                "report_ref": ADAPTER_CONFORMANCE_REPORT.as_posix(),
                "report_sha256": _sha256(root / ADAPTER_CONFORMANCE_REPORT),
                "report_id": adapter_conformance.get("report_id"),
                "candidate_identity_matches_retained_source_and_image": (
                    adapter_conformance_candidate_identity_matches
                ),
                "candidate_openapi_sha256": adapter_conformance_contract.get(
                    "live_openapi_sha256"
                ),
                "approved_openapi_sha256": adapter_conformance_contract.get(
                    "approved_openapi_sha256"
                ),
                "schema_matches_approved_contract": adapter_conformance_contract.get(
                    "live_schema_matches_approved_contract"
                ),
                "non_model_lifecycle_operations_passed": (
                    adapter_conformance_non_model_lifecycle_passed
                ),
                "prompt_async_status": adapter_conformance_operations.get(
                    "prompt_async", {}
                ).get("status"),
                "tool_mediation_status": adapter_conformance_operations.get(
                    "tool_mediation", {}
                ).get("status"),
                "provider_request_count": adapter_conformance.get("environment", {})
                .get("external_requests", {})
                .get("provider_requests"),
                "decision": adapter_conformance.get("decision"),
                "promotion_compatible": False,
            },
        },
        "security_evidence": {
            "current_manifest_refs": [worker["governance"]["security_scan_evidence_ref"] for worker in workers],
            "candidate_report_ref": (R4_DIR / "candidate-certification.json").as_posix(),
            "candidate_gate": cert_status,
            "human_or_security_disposition_required": human_security_review.get(
                "human_or_security_disposition_required"
            ),
        },
        "persisted_candidate": {
            "identity_observed": persisted_candidate_present,
            "coding_worker_manifest_pins_candidate": persisted.get("coding_worker_manifest_pins_candidate"),
            "tester_manifest_pins_candidate": persisted.get("tester_manifest_pins_candidate"),
            "retained_live_worker": {
                "worker_id": retained_worker.get("worker_id"),
                "status": retained_worker.get("status"),
                "version_pin": retained_worker.get("version_pin"),
                "evaluation_status": retained_worker.get("evaluation_status"),
                "active_shell_version_id_present": bool(retained_worker.get("active_shell_version_id")),
                "active_adapter_id_present": bool(retained_worker.get("active_adapter_id")),
                "active_skill_bundle_id_present": bool(retained_worker.get("active_skill_bundle_id")),
            },
            "steward_candidate_status": "not_observed_persisted",
            "observation_ref": RETAINED_REPORT.as_posix(),
            "observation_date": candidate_probe.get("readiness_blocker_revalidation", {}).get(
                "observation_date"
            ),
        },
        "checks": {
            "candidate_source_and_archive_identity": "PASS" if exact_source_match else "FAIL",
            "candidate_immutable_image_identity": "PASS" if image_identity_match else "FAIL",
            "active_manifest_candidate_alignment": "PASS" if manifest_versions_match else "BLOCKED",
            "active_runtime_candidate_alignment": "PASS" if runtime_versions_match_candidate else "BLOCKED",
            "candidate_adapter_compatibility": "PASS" if adapter_report_match_candidate else "BLOCKED",
            "candidate_non_model_adapter_lifecycle": (
                "PASS"
                if adapter_conformance_candidate_identity_matches
                and adapter_conformance_non_model_lifecycle_passed
                else "BLOCKED"
            ),
            "candidate_security_and_steward_governance": "PASS" if governance_ready else "BLOCKED",
            "persisted_candidate_registration": "PASS" if persisted_candidate_present else "BLOCKED",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="write JSON to this path; defaults to stdout")
    args = parser.parse_args()
    try:
        report = build_report()
    except (OSError, KeyError, TypeError, ValueError, yaml.YAMLError, json.JSONDecodeError) as exc:
        print(json.dumps({"schema_version": SCHEMA_VERSION, "status": "fail", "error": type(exc).__name__}), file=sys.stderr)
        return 1
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
