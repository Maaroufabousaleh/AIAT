"""Run the existing source-bound worker scanner for CI evidence.

This scans only the checked-in deterministic worker source and recipe. It does
not register a worker, persist governance state, or grant admission.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

MAS_ROOT = Path(__file__).resolve().parents[1]
for import_root in (MAS_ROOT / "apps" / "orchestrator-api", MAS_ROOT / "packages" / "mas-core"):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

_TOP_LEVEL_FIELDS = frozenset(
    {
        "schema_version",
        "status",
        "finding_review_status",
        "candidate",
        "manifest_sha256",
        "image_build_provenance",
        "source_files",
        "semgrep_config_sha256",
        "semgrep_config_snapshot_mode",
        "scanners",
        "finding_count",
        "scanner_error_count",
        "scan_started_at",
        "scan_completed_at",
        "report_sha256",
    }
)
_SCANNER_FIELDS = frozenset(
    {
        "name",
        "version",
        "exit_code",
        "status",
        "finding_count",
        "scanner_error_count",
        "scanner_errors",
        "findings",
        "stdout_bytes",
        "stderr_bytes",
        "result_sha256",
        "stderr_sha256",
    }
)
_FINDING_FIELDS = frozenset({"scanner", "rule_id", "path", "line", "severity", "fingerprint"})


def safe_report(report: dict[str, Any]) -> dict[str, Any]:
    """Keep only the scanner's redacted fields; exclude raw output and excerpts."""
    result = {key: value for key, value in report.items() if key in _TOP_LEVEL_FIELDS}
    scanners = report.get("scanners")
    if isinstance(scanners, list):
        result["scanners"] = []
        for scanner in scanners:
            if not isinstance(scanner, dict):
                continue
            row = {key: value for key, value in scanner.items() if key in _SCANNER_FIELDS}
            findings = scanner.get("findings")
            if isinstance(findings, list):
                row["findings"] = [
                    {key: value for key, value in finding.items() if key in _FINDING_FIELDS}
                    for finding in findings
                    if isinstance(finding, dict)
                ]
            result["scanners"].append(row)
    return result


def _candidate_identity() -> tuple[dict[str, Any], dict[str, Any]]:
    from mas_core.worker_registry.source_bound_security_scan import (
        FIRST_PARTY_IMAGE_DIGEST,
        FIRST_PARTY_PERMISSION_GRANTS,
        FIRST_PARTY_RUNTIME_ADAPTER_CONFIG,
        FIRST_PARTY_RUNTIME_SHA256,
        FIRST_PARTY_SOURCE_REPOSITORY,
        FIRST_PARTY_SOURCE_REVISION,
        FIRST_PARTY_TOOL_GRANTS,
        FIRST_PARTY_WORKER_ID,
        FIRST_PARTY_WORKER_VERSION,
    )

    worker = {
        "id": "ci-unregistered-candidate",
        "name": FIRST_PARTY_WORKER_ID,
        "version": FIRST_PARTY_WORKER_VERSION,
        "version_pin": f"{FIRST_PARTY_WORKER_VERSION}+{FIRST_PARTY_IMAGE_DIGEST.replace(':', '.')}",
        "source_repo": FIRST_PARTY_SOURCE_REPOSITORY,
        "source_revision": FIRST_PARTY_SOURCE_REVISION,
        "adapter_type": "oci",
        "adapter_entrypoint": "ProcessAdapter",
        "sandbox_profile": "sandboxed",
        "isolation_mode": "wrapper",
        "wrapper_config": {},
        "model_mode": "none",
        "model_profile_id": None,
        "tool_grants": list(FIRST_PARTY_TOOL_GRANTS),
        "permission_grants": list(FIRST_PARTY_PERMISSION_GRANTS),
        "adapter_config": {
            **FIRST_PARTY_RUNTIME_ADAPTER_CONFIG,
            "legacy_external_wrapper": False,
        },
    }
    provenance = {
        "canonical_source_repository": FIRST_PARTY_SOURCE_REPOSITORY,
        "source_provider": "aiat",
        "exact_release": FIRST_PARTY_WORKER_VERSION,
        "commit_sha": FIRST_PARTY_SOURCE_REVISION,
        "oci_image_digest": FIRST_PARTY_IMAGE_DIGEST,
        "transport_type": "oci",
        "runtime_fingerprint": f"sha256:{FIRST_PARTY_RUNTIME_SHA256}",
    }
    return worker, provenance


async def _run_scan() -> dict[str, Any]:
    from orchestrator_api.worker_security_scan import (
        WorkerSecurityScanError,
        run_first_party_worker_source_scan,
    )

    worker, provenance = _candidate_identity()
    try:
        report = await run_first_party_worker_source_scan(worker=worker, provenance=provenance)
    except WorkerSecurityScanError as exc:
        return {
            "schema_version": "aiat.first-party-worker-source-scan-ci.v1",
            "status": "blocked",
            "reason": exc.code,
        }
    return safe_report(report)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = asyncio.run(_run_scan())
    except Exception as exc:
        report = {
            "schema_version": "aiat.first-party-worker-source-scan-ci.v1",
            "status": "blocked",
            "reason": "source_scan_internal_error",
            "error_type": type(exc).__name__,
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with args.output.open("x", encoding="utf-8") as handle:
            json.dump(report, handle, sort_keys=True, indent=2)
            handle.write("\n")
    except FileExistsError:
        parser.error("refusing to overwrite existing scan evidence")
    print(
        json.dumps(
            {
                "status": report.get("status", "blocked"),
                "finding_count": report.get("finding_count", 0),
                "scanner_error_count": report.get("scanner_error_count", 0),
            },
            sort_keys=True,
        )
    )
    return 0 if report.get("status") == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
