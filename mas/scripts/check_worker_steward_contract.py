"""Exercise the steward lifecycle contract for externally sourced workers.

The fixture loads the checked-in externally sourced worker manifests and runs
the real AIAT ``ExternalWorkerSteward`` domain through dedicated-steward
creation, immutable candidate generation, compatibility-matrix recording,
certification, approval, shadow/canary/promotion, and rollback.  It is
deterministic domain evidence only: it does not claim a database row, a
runtime import, a security scan, a sandbox smoke test, a live canary, or a
production worker run.  Licence/restriction fields are not used as gates.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import yaml
from pydantic import ValidationError

from mas_core.protocols.worker_manifest import WorkerManifest
from mas_core.worker_registry.steward import (
    CandidateIntakeStatus,
    CompatibilityMatrix,
    ExternalProvenance,
    ExternalWorkerSteward,
    RolloutStatus,
    StewardStatus,
    StewardTransitionError,
)

MAS_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORKERS_DIR = MAS_ROOT / "workers"
CHECK_SCHEMA = "aiat.worker-steward-contract-check.v1"
_STAGES = (
    CandidateIntakeStatus.SOURCE_REVIEW,
    CandidateIntakeStatus.SECURITY_REVIEW,
    CandidateIntakeStatus.INTERFACE_RESEARCH,
    CandidateIntakeStatus.GENERATED,
    CandidateIntakeStatus.CERTIFYING,
)


def _external_manifests(workers_dir: Path) -> tuple[list[WorkerManifest], list[str]]:
    manifests: list[WorkerManifest] = []
    errors: list[str] = []
    for path in sorted(workers_dir.glob("*.yaml")):
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            manifest = WorkerManifest.model_validate(raw)
        except (OSError, ValueError, yaml.YAMLError, ValidationError) as exc:
            errors.append(f"{path.name}: {type(exc).__name__}")
            continue
        source_repo = str(manifest.metadata.source_repo or "").strip().lower()
        if source_repo not in {"", "local"}:
            manifests.append(manifest)
    return manifests, errors


def _provenance(manifest: WorkerManifest) -> ExternalProvenance:
    raw = dict(manifest.source_provenance or {})
    raw.setdefault("canonical_source_repository", manifest.metadata.source_repo)
    raw.setdefault("source_provider", "github" if "github.com" in str(manifest.metadata.source_repo) else "external")
    raw.setdefault("exact_release", manifest.metadata.version_pin)
    raw.setdefault("transport_type", manifest.runtime.transport)
    raw.setdefault("adapter_version", manifest.integration.certified_adapter_version or manifest.metadata.version)
    raw.setdefault("protocol_api_version", manifest.integration.contract_version)
    return ExternalProvenance.model_validate(raw)


def _exercise_worker(manifest: WorkerManifest) -> dict[str, Any]:
    source_provenance = _provenance(manifest)
    # The fixture supplies a synthetic passing security observation solely so
    # the domain lifecycle can be exercised.  The manifest's real security
    # status is returned separately and never overwritten.
    fixture_provenance = source_provenance.model_copy(update={"security_scan_status": "passed"})
    steward = ExternalWorkerSteward(
        worker_id=manifest.metadata.id,
        provenance=fixture_provenance,
        steward_id=uuid5(NAMESPACE_URL, f"aiat.steward:{manifest.metadata.id}"),
    )
    steward.transition(StewardStatus.READY, actor="contract-fixture")
    version = str(manifest.metadata.version_pin or manifest.metadata.version or "0.0.0")
    adapter_version = str(manifest.integration.certified_adapter_version or "1.0.0")
    candidate = steward.generate_candidate(
        semantic_version=version,
        adapter_version=adapter_version,
        upstream_compatibility_range=f"=={version}",
        adapter_entrypoint=manifest.integration.adapter_entrypoint,
        implementation_ref=str(manifest.metadata.source_revision or "working-tree"),
    )
    matrix = steward.record_compatibility_matrix(
        CompatibilityMatrix(
            runtime_version=version,
            adapter_version=adapter_version,
            contract_version=str(manifest.integration.contract_version or "aiat.adapter.v1"),
            fixtures=("worker_contract", "adapter_conformance", "sandbox", "canary", "rollback"),
            passed=True,
        )
    )
    for stage in _STAGES:
        steward.advance_candidate(candidate.candidate_id, stage)
    certification = steward.certify_candidate(candidate.candidate_id, conformance={"passed": True}, checks={})
    steward.approve_candidate(candidate.candidate_id)
    rollout = steward.start_rollout(
        candidate.candidate_id,
        actor="contract-fixture",
        eligible_task_classes=["read_only"],
    )
    steward.advance_rollout(rollout.rollout_id, RolloutStatus.SHADOW, sample_count=10)
    steward.advance_rollout(rollout.rollout_id, RolloutStatus.CANARY, sample_count=10)
    steward.advance_rollout(rollout.rollout_id, RolloutStatus.PROMOTING, sample_count=5)
    steward.advance_rollout(
        rollout.rollout_id,
        RolloutStatus.ACTIVE,
        sample_count=3,
        metrics={"regression_fraction": 0.0},
    )
    baseline_bundle_id = str(steward.active_bundle.bundle_id) if steward.active_bundle else None

    # Exercise the real regression gate on a fresh immutable candidate.  The
    # failed promotion is intentionally rolled back before activation; the
    # steward must retain the first candidate's active pointers.
    replacement = steward.generate_candidate(
        semantic_version=f"{version}-replacement",
        adapter_version=adapter_version,
        upstream_compatibility_range=f"=={version}",
        adapter_entrypoint=manifest.integration.adapter_entrypoint,
        implementation_ref=str(manifest.metadata.source_revision or "working-tree"),
    )
    for stage in _STAGES:
        steward.advance_candidate(replacement.candidate_id, stage)
    replacement_certification = steward.certify_candidate(
        replacement.candidate_id,
        conformance={"passed": True},
        checks={},
    )
    steward.approve_candidate(replacement.candidate_id)
    replacement_rollout = steward.start_rollout(
        replacement.candidate_id,
        actor="contract-fixture",
        eligible_task_classes=["read_only"],
    )
    steward.advance_rollout(replacement_rollout.rollout_id, RolloutStatus.SHADOW, sample_count=10)
    steward.advance_rollout(replacement_rollout.rollout_id, RolloutStatus.CANARY, sample_count=10)
    steward.advance_rollout(replacement_rollout.rollout_id, RolloutStatus.PROMOTING, sample_count=5)
    try:
        steward.advance_rollout(
            replacement_rollout.rollout_id,
            RolloutStatus.ACTIVE,
            sample_count=3,
            metrics={"regression_fraction": 0.5},
        )
    except StewardTransitionError:
        regression_blocked = True
    else:
        regression_blocked = False
    steward.rollback(replacement_rollout.rollout_id, reason="contract fixture regression")
    checks = {
        "dedicated_steward": candidate.steward_id == steward.steward_id,
        "immutable_candidate": bool(candidate.bundle.content_hash and candidate.adapter.content_hash),
        "compatibility_matrix": matrix in steward.compatibility_matrices and matrix.passed,
        "certification": certification.passed,
        "replacement_certification": replacement_certification.passed,
        "regression_block": regression_blocked and replacement_rollout.status == RolloutStatus.ROLLED_BACK,
        "rollback": (
            rollout.status == RolloutStatus.ACTIVE
            and replacement_rollout.status == RolloutStatus.ROLLED_BACK
            and baseline_bundle_id is not None
            and steward.active_bundle is not None
            and str(steward.active_bundle.bundle_id) == baseline_bundle_id
        ),
    }
    return {
        "worker_id": manifest.metadata.id,
        "source_repo": manifest.metadata.source_repo,
        "steward_id": str(steward.steward_id),
        "real_security_scan_status": str((manifest.source_provenance or {}).get("security_scan_status") or "not_recorded"),
        "checks": checks,
        "rollout": {
            "baseline_status": rollout.status,
            "replacement_status": replacement_rollout.status,
            "regression_blocked": regression_blocked,
            "active_bundle_preserved": bool(
                baseline_bundle_id
                and steward.active_bundle
                and str(steward.active_bundle.bundle_id) == baseline_bundle_id
            ),
        },
        "status": "pass" if all(checks.values()) else "fail",
    }


def build_report(*, workers_dir: Path = DEFAULT_WORKERS_DIR) -> dict[str, Any]:
    manifests, errors = _external_manifests(workers_dir)
    rows: list[dict[str, Any]] = []
    for manifest in manifests:
        try:
            rows.append(_exercise_worker(manifest))
        except (ValueError, TypeError, ValidationError) as exc:
            errors.append(f"{manifest.metadata.id}: {type(exc).__name__}")
    if not manifests:
        errors.append("no externally sourced worker manifests found")
    failed_rows = [row["worker_id"] for row in rows if row["status"] != "pass"]
    if failed_rows:
        errors.extend(f"{worker_id}: lifecycle contract failed" for worker_id in failed_rows)
    return {
        "schema_version": CHECK_SCHEMA,
        "status": "fail" if errors else "pass",
        "external_worker_count": len(manifests),
        "rows": rows,
        "errors": sorted(errors),
        "boundary": {
            "steward_candidate_matrix_rollout_rollback": "domain_fixture_checked",
            "runtime_imports": "not_checked",
            "security_scan": "not_checked",
            "sandbox": "not_checked",
            "live_canary": "not_checked",
            "live_worker_run": "not_checked",
            "database_persistence": "not_checked",
            "licence_metadata": "informational_only",
        },
        "policy": {
            "programme_scope": "personal-internal-only",
            "licence_metadata_is_gate": False,
        },
        "scope": "externally sourced default-worker steward lifecycle domain contract only",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers-dir", type=Path, default=DEFAULT_WORKERS_DIR)
    parser.add_argument("--json", action="store_true", help="emit the machine-readable report")
    args = parser.parse_args(argv)
    report = build_report(workers_dir=args.workers_dir)
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        print(
            f"worker-steward-contract: {report['status']} — "
            f"external_workers={report['external_worker_count']} errors={len(report['errors'])}"
        )
        for error in report["errors"]:
            print(f"worker-steward-contract: {error}", file=sys.stderr)
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
