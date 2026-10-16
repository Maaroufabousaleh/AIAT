"""Validation for the server-generated deterministic-worker security scan.

The report is deliberately narrow: it recognizes only the checked-in
``aiat_deterministic_file_worker`` candidate and the scanner toolchain pinned
for that candidate.  The scanner route is the only normal writer.  This
module validates source-only scan evidence; the report does not establish
that the OCI image was built from the scanned source, and cannot alone satisfy
the candidate security gate.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any

SOURCE_BOUND_SCAN_SCHEMA = "aiat.first-party-worker-source-security-scan.v1"


class SourceBoundSecurityScanPersistenceError(ValueError):
    """A stable, bounded failure while storing source-scan evidence."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)

FIRST_PARTY_WORKER_ID = "aiat_deterministic_file_worker"
FIRST_PARTY_WORKER_VERSION = "0.1.1"
FIRST_PARTY_IMAGE_SOURCE_BINDING_REQUIRED = "image_source_binding_required"
FIRST_PARTY_SOURCE_REPOSITORY = "https://github.com/Maaroufabousaleh/AIAT"
FIRST_PARTY_SOURCE_REVISION = "1855a75fb17243d8298b84468372e7a6e496da46"
FIRST_PARTY_IMAGE_DIGEST = (
    "sha256:fb1a347b40350519451872e6d4d818da757355d0bc3c7314f74563bed42407cc"
)
FIRST_PARTY_RUNTIME_SHA256 = (
    "3b75bea1aa232ac5362d46b448cdc789a55f6e87e955ce4053a7d849ac858784"
)
FIRST_PARTY_DOCKERFILE_SHA256 = (
    "8928573b6889adcbee09ec7cd527d919682254ed725dabc7919a04ef6ccc6d26"
)
FIRST_PARTY_MANIFEST_SHA256 = (
    "a62f4d8d5e91df650957f60cf121ba141b79fdb6206354ebfe25fb00e5455eca"
)
FIRST_PARTY_SEMGREP_CONFIG_SHA256 = (
    "f19daaaf7ab49feeb366b87cf0e31b33c009f98c9c3c6f1fd836c286b594417a"
)
FIRST_PARTY_RUNTIME_ADAPTER_CONFIG = {
    "image": f"aiat-deterministic-file-worker@{FIRST_PARTY_IMAGE_DIGEST}",
    "sandbox_profile": "sandboxed",
    "sandbox_runtime": "runsc",
    "bidirectional_tool_protocol": True,
    "tool_response_timeout_seconds": 30,
    "runtime_version": "aiat-deterministic-file-worker/0.1.1",
    "execution_policy": {
        "trust": "untrusted",
        "formats": ["oci", "linux"],
        "preferred_runtimes": ["runsc"],
        "allowed_runtimes": [],
        "minimum_isolation": "sandboxed",
        "gvisor_compatible": True,
        "remote_vm_allowed": False,
        "network": "egress-allowlist",
        "filesystem": {},
    },
}
FIRST_PARTY_TOOL_GRANTS = ["file_write"]
FIRST_PARTY_PERMISSION_GRANTS = ["project.workspace.write"]
_WORKER_ADAPTER_CONFIG_LIFECYCLE_FIELDS = frozenset(
    {
        "activation_blockers",
        "governance_required",
        "governance_status",
        "legacy_external_wrapper",
        "migration_status",
        "steward_id",
    }
)
PINNED_SCANNERS = {
    "semgrep": {
        "version": "1.168.0",
        "executable": "/usr/local/bin/semgrep",
        "max_stdout_bytes": 2 * 1024 * 1024,
        "max_stderr_bytes": 64 * 1024,
    },
    "trufflehog": {
        "version": "3.97.0",
        "executable": "/usr/local/bin/trufflehog",
        "max_stdout_bytes": 2 * 1024 * 1024,
        "max_stderr_bytes": 64 * 1024,
    },
}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def canonical_sha256(value: Any) -> str:
    """Hash a JSON value using the stable encoding used by scan reports."""
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def finalize_source_bound_scan_report(report: dict[str, Any]) -> dict[str, Any]:
    """Return a report with its self-hash calculated over all other fields."""
    result = dict(report)
    result.pop("report_sha256", None)
    result["report_sha256"] = canonical_sha256(result)
    return result


def source_bound_security_scan_required(
    provenance: Mapping[str, Any] | Any,
    *,
    worker: Mapping[str, Any] | None = None,
) -> bool:
    """Scope this scanner gate to the pinned deterministic first-party worker.

    Other external workers retain their existing governance/security path;
    this report format intentionally covers only one exact worker candidate.
    A deterministic registry row still requires the report if its persisted
    provenance has drifted, so changing provenance cannot opt that worker out.
    """
    values = _provenance_mapping(provenance) or {}
    if isinstance(worker, Mapping) and worker.get("name") == FIRST_PARTY_WORKER_ID:
        return True
    return all(
        values.get(key) == value
        for key, value in {
            "canonical_source_repository": FIRST_PARTY_SOURCE_REPOSITORY,
            "source_provider": "aiat",
            "exact_release": FIRST_PARTY_WORKER_VERSION,
            "commit_sha": FIRST_PARTY_SOURCE_REVISION,
            "oci_image_digest": FIRST_PARTY_IMAGE_DIGEST,
            "transport_type": "oci",
            "runtime_fingerprint": f"sha256:{FIRST_PARTY_RUNTIME_SHA256}",
        }.items()
    )


def source_bound_security_scan_is_valid(
    report: Any,
    *,
    expected_worker_id: str | None = None,
    provenance: Mapping[str, Any] | Any | None = None,
    current_worker: Mapping[str, Any] | None = None,
) -> bool:
    """Return whether source scan evidence satisfies the full security gate.

    A clean scan of the source checkout does not establish that the OCI image
    digest is built from that source.  AIAT does not currently have a trusted
    OCI provenance verifier for this candidate, so this gate deliberately
    remains closed even when the source-only report is valid.
    """
    if not source_bound_security_scan_result_is_valid(
        report,
        expected_worker_id=expected_worker_id,
        provenance=provenance,
        current_worker=current_worker,
    ):
        return False
    # The report's declared digest is not independent evidence of a
    # source-to-image build relationship.
    return False


def source_bound_security_scan_result_is_valid(
    report: Any,
    *,
    expected_worker_id: str | None = None,
    provenance: Mapping[str, Any] | Any | None = None,
    current_worker: Mapping[str, Any] | None = None,
) -> bool:
    """Validate the source-only scan result, without asserting image binding."""
    if not source_bound_security_scan_report_integrity_is_valid(
        report,
        expected_worker_id=expected_worker_id,
        provenance=provenance,
        current_worker=current_worker,
    ):
        return False
    assert isinstance(report, Mapping)
    if report.get("status") != "passed" or report.get("finding_review_status") != "not_required":
        return False
    if report.get("finding_count") != 0 or report.get("scanner_error_count") != 0:
        return False
    for row in report.get("scanners", []):
        if (
            not isinstance(row, Mapping)
            or row.get("status") != "pass"
            or row.get("exit_code") != 0
            or row.get("finding_count") != 0
            or row.get("scanner_error_count") != 0
            or row.get("findings") != []
            or row.get("scanner_errors") != []
        ):
            return False
    return True


def source_bound_security_scan_report_integrity_is_valid(
    report: Any,
    *,
    expected_worker_id: str | None,
    provenance: Mapping[str, Any] | Any | None = None,
    current_worker: Mapping[str, Any] | None = None,
) -> bool:
    """Validate identity, scanner provenance, hashes, and normalized findings.

    This also accepts a correctly formed blocked or findings-bearing scan so
    that negative evidence can be retained. It never treats such a report as
    passing technical security review.
    """
    if not isinstance(report, Mapping):
        return False
    if report.get("schema_version") != SOURCE_BOUND_SCAN_SCHEMA:
        return False
    if report.get("status") not in {"passed", "findings_review_required", "blocked"}:
        return False
    if report.get("finding_review_status") not in {
        "not_required",
        "requires_human_review",
        "not_evaluated",
    }:
        return False

    report_digest = report.get("report_sha256")
    unhashed = dict(report)
    unhashed.pop("report_sha256", None)
    if not isinstance(report_digest, str) or not _SHA256.fullmatch(report_digest):
        return False
    if canonical_sha256(unhashed) != report_digest:
        return False

    candidate = report.get("candidate")
    if not isinstance(candidate, Mapping):
        return False
    expected_candidate = {
        "manifest_worker_id": FIRST_PARTY_WORKER_ID,
        "version": FIRST_PARTY_WORKER_VERSION,
        "source_repository": FIRST_PARTY_SOURCE_REPOSITORY,
        "source_revision": FIRST_PARTY_SOURCE_REVISION,
        "oci_image_digest": FIRST_PARTY_IMAGE_DIGEST,
        "runtime_sha256": FIRST_PARTY_RUNTIME_SHA256,
        "dockerfile_sha256": FIRST_PARTY_DOCKERFILE_SHA256,
    }
    if any(candidate.get(key) != value for key, value in expected_candidate.items()):
        return False
    if report.get("manifest_sha256") != FIRST_PARTY_MANIFEST_SHA256:
        return False
    registry_worker_id = candidate.get("worker_registry_id")
    if not isinstance(registry_worker_id, str) or not registry_worker_id:
        return False
    if expected_worker_id is None or registry_worker_id != str(expected_worker_id):
        return False
    if current_worker is not None and not source_bound_worker_identity_is_valid(
        current_worker,
        report,
        expected_worker_id=expected_worker_id,
    ):
        return False

    provenance_values = _provenance_mapping(provenance)
    if provenance_values is not None:
        expected_provenance = {
            "canonical_source_repository": FIRST_PARTY_SOURCE_REPOSITORY,
            "source_provider": "aiat",
            "exact_release": FIRST_PARTY_WORKER_VERSION,
            "commit_sha": FIRST_PARTY_SOURCE_REVISION,
            "oci_image_digest": FIRST_PARTY_IMAGE_DIGEST,
            "transport_type": "oci",
            "runtime_fingerprint": f"sha256:{FIRST_PARTY_RUNTIME_SHA256}",
        }
        if any(provenance_values.get(key) != value for key, value in expected_provenance.items()):
            return False
        embedded = provenance_values.get("source_bound_security_scan")
        if embedded is not None and embedded != report:
            return False

    source_files = report.get("source_files")
    if not isinstance(source_files, list):
        return False
    source_hashes = {
        row.get("path"): row.get("sha256")
        for row in source_files
        if isinstance(row, Mapping)
    }
    if source_hashes != {
        "mas/workers/aiat_deterministic_file_worker_process.py": FIRST_PARTY_RUNTIME_SHA256,
        "mas/workers/Dockerfile.aiat_deterministic_file_worker": FIRST_PARTY_DOCKERFILE_SHA256,
    }:
        return False
    if report.get("semgrep_config_sha256") != FIRST_PARTY_SEMGREP_CONFIG_SHA256:
        return False
    if not isinstance(report.get("scan_started_at"), str) or not isinstance(
        report.get("scan_completed_at"), str
    ):
        return False
    if not isinstance(report.get("finding_count"), int) or report["finding_count"] < 0:
        return False
    if not isinstance(report.get("scanner_error_count"), int) or report["scanner_error_count"] < 0:
        return False

    scanners = report.get("scanners")
    if not isinstance(scanners, list) or len(scanners) != len(PINNED_SCANNERS):
        return False
    scanner_by_name = {
        row.get("name"): row for row in scanners if isinstance(row, Mapping)
    }
    if set(scanner_by_name) != set(PINNED_SCANNERS):
        return False
    source_dir = report.get("scanner_source_dir")
    if not isinstance(source_dir, str) or not source_dir.startswith(
        "/tmp/aiat-worker-security-scan-"
    ):
        return False
    semgrep_config_path = report.get("semgrep_config_path")
    expected_config_snapshot_path = f"{source_dir.rsplit('/', 1)[0]}/semgrep-default.yml"
    if semgrep_config_path != expected_config_snapshot_path:
        return False
    semgrep_config_source_path = report.get("semgrep_config_source_path")
    if semgrep_config_source_path != "/app/semgrep-default.yml" and not (
        isinstance(semgrep_config_source_path, str)
        and semgrep_config_source_path.endswith(
            "/apps/tool-service/tool_service/semgrep-default.yml"
        )
    ):
        return False
    if report.get("semgrep_config_snapshot_mode") != "0444":
        return False
    expected_argv = {
        "semgrep": [
            PINNED_SCANNERS["semgrep"]["executable"],
            "scan",
            "--config",
            semgrep_config_path,
            "--json",
            "--metrics=off",
            "--disable-version-check",
            "--no-git-ignore",
            "--quiet",
            source_dir,
        ],
        "trufflehog": [
            PINNED_SCANNERS["trufflehog"]["executable"],
            "filesystem",
            "--json",
            "--no-update",
            "--no-verification",
            "--log-level=-1",
            source_dir,
        ],
    }
    for name, pin in PINNED_SCANNERS.items():
        row = scanner_by_name[name]
        if row.get("version") != pin["version"]:
            return False
        if row.get("argv") != expected_argv[name]:
            return False
        if row.get("version_argv") != [pin["executable"], "--version"]:
            return False
        if row.get("status") not in {"pass", "findings_review_required", "blocked"}:
            return False
        exit_code = row.get("exit_code")
        if exit_code is not None and (not isinstance(exit_code, int) or exit_code < 0):
            return False
        if not isinstance(row.get("finding_count"), int) or row["finding_count"] < 0:
            return False
        if not isinstance(row.get("scanner_error_count"), int) or row["scanner_error_count"] < 0:
            return False
        scanner_findings = row.get("findings")
        if not isinstance(scanner_findings, list) or len(scanner_findings) > 256:
            return False
        if len(scanner_findings) > row["finding_count"]:
            return False
        for finding in scanner_findings:
            if not isinstance(finding, Mapping) or set(finding) != {
                "finding_id",
                "rule_id",
                "path",
                "line",
                "severity",
            }:
                return False
            finding_id = finding.get("finding_id")
            if not isinstance(finding_id, str) or not _SHA256.fullmatch(finding_id):
                return False
            if finding.get("path") not in {
                "mas/workers/aiat_deterministic_file_worker_process.py",
                "mas/workers/Dockerfile.aiat_deterministic_file_worker",
            }:
                return False
            if not isinstance(finding.get("rule_id"), str) or len(finding["rule_id"]) > 200:
                return False
            if finding.get("line") is not None and (
                not isinstance(finding["line"], int) or finding["line"] < 0
            ):
                return False
            if not isinstance(finding.get("severity"), str) or len(finding["severity"]) > 32:
                return False
        scanner_errors = row.get("scanner_errors")
        if not isinstance(scanner_errors, list) or any(
            not isinstance(item, str) or len(item) > 100 for item in scanner_errors
        ):
            return False
        if len(scanner_errors) != row["scanner_error_count"]:
            return False
        if (row["finding_count"] > 0 and row.get("status") == "pass") or (
            row["scanner_error_count"] > 0 and row.get("status") != "blocked"
        ):
            return False
        if row.get("stdout_bytes", -1) < 0 or row.get("stdout_bytes", 0) > pin["max_stdout_bytes"]:
            return False
        if row.get("stderr_bytes", -1) < 0 or row.get("stderr_bytes", 0) > pin["max_stderr_bytes"]:
            return False
        for field in (
            "version_output_sha256",
            "result_sha256",
            "stderr_sha256",
        ):
            digest = row.get(field)
            if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
                return False
    scanners = list(scanner_by_name.values())
    findings_total = sum(int(row.get("finding_count") or 0) for row in scanners)
    errors_total = sum(int(row.get("scanner_error_count") or 0) for row in scanners)
    if report.get("finding_count") != findings_total or report.get("scanner_error_count") != errors_total:
        return False
    if errors_total:
        return report.get("status") == "blocked" and report.get("finding_review_status") == "not_evaluated"
    if findings_total:
        return (
            report.get("status") == "findings_review_required"
            and report.get("finding_review_status") == "requires_human_review"
        )
    if report.get("status") != "passed" or report.get("finding_review_status") != "not_required":
        return False
    # A self-consistent report must not be enough to persist a scalar PASS.
    # In particular, TruffleHog uses exit 183 to signal findings; accepting it
    # with zero parsed findings would allow inconsistent scanner output to
    # become passing evidence. Require the same clean row semantics used by
    # source_bound_security_scan_is_valid at this persistence integrity layer.
    return all(
        row.get("status") == "pass"
        and row.get("exit_code") == 0
        and row.get("finding_count") == 0
        and row.get("scanner_error_count") == 0
        and row.get("findings") == []
        and row.get("scanner_errors") == []
        for row in scanners
    )


def source_bound_worker_identity_is_valid(
    worker: Mapping[str, Any] | Any,
    report: Mapping[str, Any] | Any,
    *,
    expected_worker_id: str | None,
) -> bool:
    """Compare a report to the current registry identity, not just old provenance."""
    if not isinstance(worker, Mapping) or not isinstance(report, Mapping):
        return False
    candidate = report.get("candidate")
    if not isinstance(candidate, Mapping):
        return False
    worker_id = str(worker.get("id") or "")
    config = worker.get("adapter_config")
    if not isinstance(config, Mapping):
        return False
    runtime_config = {
        key: value
        for key, value in config.items()
        if key not in _WORKER_ADAPTER_CONFIG_LIFECYCLE_FIELDS
    }
    if canonical_sha256(runtime_config) != canonical_sha256(
        FIRST_PARTY_RUNTIME_ADAPTER_CONFIG
    ):
        return False
    if "governance_required" in config and config.get("governance_required") is not True:
        return False
    if "governance_status" in config and config.get("governance_status") != "compatibility":
        return False
    if "migration_status" in config and config.get("migration_status") != "native":
        return False
    # This exact worker is backed by the OCIAdapter.  The marker is a
    # lifecycle classification, not part of the immutable runtime payload;
    # require the seeder's non-legacy classification while retaining all
    # source, image, sandbox, protocol, and grant checks below.
    if config.get("legacy_external_wrapper") is not False:
        return False
    if "activation_blockers" in config and (
        not isinstance(config.get("activation_blockers"), list)
        or any(not isinstance(item, str) for item in config["activation_blockers"])
    ):
        return False
    if "steward_id" in config and not str(config.get("steward_id") or "").strip():
        return False

    tool_grants = worker.get("tool_grants")
    permission_grants = worker.get("permission_grants")
    expected_pin = (
        f"{FIRST_PARTY_WORKER_VERSION}+"
        f"{FIRST_PARTY_IMAGE_DIGEST.replace(':', '.')}"
    )
    return bool(
        worker_id
        and expected_worker_id is not None
        and worker_id == str(expected_worker_id)
        and candidate.get("worker_registry_id") == worker_id
        and str(worker.get("name") or "") == FIRST_PARTY_WORKER_ID
        and str(worker.get("version") or "") == FIRST_PARTY_WORKER_VERSION
        and str(worker.get("version_pin") or "") == expected_pin
        and str(worker.get("source_repo") or "") == FIRST_PARTY_SOURCE_REPOSITORY
        and str(worker.get("source_revision") or "") == FIRST_PARTY_SOURCE_REVISION
        and str(worker.get("adapter_type") or "").lower() == "oci"
        and str(worker.get("adapter_entrypoint") or "") == "ProcessAdapter"
        and worker.get("adapter_module") is None
        and str(worker.get("sandbox_profile") or "") == "sandboxed"
        and str(worker.get("isolation_mode") or "") == "wrapper"
        and worker.get("wrapper_config") == {}
        and str(worker.get("model_mode") or "") == "none"
        and worker.get("model_profile_id") in (None, "")
        and (
            "tool_grants" not in worker
            or tool_grants == FIRST_PARTY_TOOL_GRANTS
        )
        and (
            "permission_grants" not in worker
            or permission_grants == FIRST_PARTY_PERMISSION_GRANTS
        )
        and candidate.get("manifest_worker_id") == FIRST_PARTY_WORKER_ID
        and candidate.get("version") == FIRST_PARTY_WORKER_VERSION
        and candidate.get("source_repository") == FIRST_PARTY_SOURCE_REPOSITORY
        and candidate.get("source_revision") == FIRST_PARTY_SOURCE_REVISION
        and candidate.get("oci_image_digest") == FIRST_PARTY_IMAGE_DIGEST
        and candidate.get("runtime_sha256") == FIRST_PARTY_RUNTIME_SHA256
        and candidate.get("dockerfile_sha256") == FIRST_PARTY_DOCKERFILE_SHA256
    )


def _provenance_mapping(value: Mapping[str, Any] | Any | None) -> Mapping[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        return value
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        result = dump(mode="json")
        return result if isinstance(result, Mapping) else None
    return None
