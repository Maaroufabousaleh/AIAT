"""Run the deterministic project evidence-package fixture.

The fixture proves that repository, document, test, security, deployment,
cost, approval, flow, worker, artifact, and audit sources can be grouped in
one read model while licence/restriction values remain notices only.  ``--live``
is fail-closed until an authenticated project/API evidence run is explicitly
configured; the fixture never mutates project or deployment state.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from mas_core.workflow import EvidencePolicy, build_evidence_package, evaluate_project_evidence

CHECK_SCHEMA = "aiat.project-evidence-package-check.v1"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON evidence")
    parser.add_argument(
        "--live",
        action="store_true",
        help="require an authenticated live project evidence integration",
    )
    return parser


def _fixture() -> dict[str, Any]:
    policy = EvidencePolicy(
        policy_id="release-package",
        version="1.0",
        requires_artifacts=True,
        required_artifact_kinds=("test-report", "security-scan", "deployment"),
        requires_repository=True,
        requires_approvals_closed=False,
        requires_audit=False,
    )
    documents = [
        {"id": "doc-pdr", "doc_type": "PDR", "status": "APPROVED", "version": 1}
    ]
    artifacts = [
        {
            "id": "artifact-test",
            "kind": "test-report",
            "path": "reports/tests.json",
            "sha256": "test-sha",
            "metadata": {},
        },
        {
            "id": "artifact-security",
            "kind": "security-scan",
            "path": "reports/security.json",
            "sha256": "security-sha",
            "metadata": {"license": "internal-use-notice"},
        },
        {
            "id": "artifact-deployment",
            "kind": "deployment",
            "path": "release/deployment.json",
            "sha256": "deployment-sha",
            "metadata": {},
        },
    ]
    repository = {
        "id": "repository-1",
        "repository_mode": "init",
        "initialized": True,
        "adapter_health": "ok",
        "branch": "main",
        "head_commit": "deadbeef",
    }
    flow_instance = {"id": "flow-instance-1", "status": "COMPLETED", "flow_version": 2}
    worker_runs = [{"id": "worker-run-1", "state": "SUCCEEDED", "worker_id": "tester"}]
    approvals = [{"id": "approval-1", "gate_type": "release", "status": "APPROVED"}]
    audit_events = [{"id": "audit-1", "event_type": "state_transition", "occurred_at": "2026-08-10T00:00:00Z"}]
    usage = {
        "available": True,
        "source": "project_usage_events",
        "llm_calls": 2,
        "tool_calls": 3,
        "total_tokens": 500,
        "total_cost_usd": 0.12,
    }
    completeness = evaluate_project_evidence(
        project_id="project-1",
        policy=policy,
        project={"id": "project-1"},
        documents=documents,
        artifacts=artifacts,
        flow_instance=flow_instance,
        approvals=approvals,
        worker_runs=worker_runs,
        repository=repository,
        audit_events=audit_events,
    )
    package = build_evidence_package(
        completeness=completeness,
        policy=policy,
        documents=documents,
        artifacts=artifacts,
        flow_instance=flow_instance,
        approvals=approvals,
        worker_runs=worker_runs,
        repository=repository,
        audit_events=audit_events,
        usage=usage,
        generated_at="2026-08-10T00:00:00+00:00",
    )
    categories = {category.category: category for category in package.categories}
    required_present = all(
        category.status == "present"
        for category in package.categories
        if category.required
    )
    return {
        "schema_version": CHECK_SCHEMA,
        "package": package.model_dump(mode="json"),
        "status": "pass" if package.status == "complete" and required_present else "fail",
        "required_categories_present": required_present,
        "category_count": len(categories),
        "scope": "deterministic fixture; no project, worker, artifact, or deployment state was mutated",
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.live:
        report: dict[str, Any] = {
            "schema_version": CHECK_SCHEMA,
            "mode": "live",
            "status": "blocked",
            "reason": "authenticated live project evidence integration is not configured",
            "scope": "no live project, worker, artifact, or deployment state was changed",
        }
        exit_code = 2
    else:
        report = {"mode": "fixture", **_fixture()}
        exit_code = 0 if report["status"] == "pass" else 1
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2, default=str))
    else:
        print(f"project evidence package: {report['status']} — {report.get('reason', report.get('scope', ''))}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
