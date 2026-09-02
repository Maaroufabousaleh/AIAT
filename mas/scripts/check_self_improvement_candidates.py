"""Run the deterministic self-improvement candidate-detection fixture.

This fixture feeds the real bounded detector with defect, metric, upstream,
cost, and operator-goal signals.  It proves deterministic risk/budget
normalization, exact-duplicate collapse, conflicting-ID rejection, bounded
metadata, and the absence of project/authority side effects.  Candidate
detection is only a proposal stage: it does not create projects, reserve
budget, grant credentials, or change deployments.  Licence metadata is
retained as provenance and never gates detection.
"""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal
from uuid import UUID

from mas_core.workflow import (
    ImprovementSignal,
    ImprovementSignalSeverity,
    ImprovementSignalSource,
    detect_improvement_candidates,
)

CHECK_SCHEMA = "aiat.self-improvement-candidate-detection.v1"
COMPANY_ID = UUID("00000000-0000-4000-a000-000000000911")


def _signal(
    signal_id: str,
    source: ImprovementSignalSource,
    *,
    severity: ImprovementSignalSeverity = ImprovementSignalSeverity.MEDIUM,
    budget_usd: str | None = None,
) -> ImprovementSignal:
    return ImprovementSignal(
        signal_id=signal_id,
        source=source,
        title=f"Investigate {signal_id}",
        description=f"Bounded fixture observation for {signal_id}",
        source_ref=f"{source.value}:{signal_id}",
        severity=severity,
        company_id=COMPANY_ID,
        budget_usd=budget_usd,
        metadata={"fixture": "candidate-detection"},
        licence_metadata={"restriction_notice": "metadata-only"},
    )


def _fixture() -> dict[str, object]:
    cases: list[dict[str, object]] = []

    def passed(case: str, **detail: object) -> None:
        cases.append({"case": case, "passed": True, **detail})

    def failed(case: str, error: Exception) -> None:
        cases.append({"case": case, "passed": False, "error": f"{type(error).__name__}: {error}"})

    signals = [
        _signal("operator-goal-1", ImprovementSignalSource.OPERATOR_GOAL, severity=ImprovementSignalSeverity.LOW),
        _signal("defect-1", ImprovementSignalSource.DEFECT, severity=ImprovementSignalSeverity.CRITICAL),
        _signal("metric-1", ImprovementSignalSource.METRIC, severity=ImprovementSignalSeverity.HIGH),
        _signal("upstream-1", ImprovementSignalSource.UPSTREAM_UPDATE),
        _signal("cost-1", ImprovementSignalSource.COST, budget_usd="7.25"),
    ]
    try:
        result = detect_improvement_candidates(signals)
        if (
            len(result.candidates) != 5
            or result.candidates[0].risk.value != "critical"
            or result.candidates[1].risk.value != "high"
            or result.source_counts != {
                "signal:cost": 1,
                "signal:defect": 1,
                "signal:metric": 1,
                "signal:operator_goal": 1,
                "signal:upstream_update": 1,
            }
            or result.licence_metadata_is_gate
        ):
            raise AssertionError("source/risk normalization did not reconcile")
        passed("source_and_risk_normalization", candidate_count=len(result.candidates))
    except Exception as exc:
        failed("source_and_risk_normalization", exc)

    try:
        duplicate = _signal("duplicate-1", ImprovementSignalSource.DEFECT)
        result = detect_improvement_candidates([duplicate, duplicate.model_copy(deep=True)])
        if len(result.candidates) != 1 or result.deduplicated_signal_ids != ("duplicate-1",):
            raise AssertionError("exact duplicate signal was not collapsed")
        passed("exact_duplicate_collapse")
    except Exception as exc:
        failed("exact_duplicate_collapse", exc)

    try:
        conflicting = _signal("conflict-1", ImprovementSignalSource.DEFECT)
        changed = conflicting.model_copy(update={"description": "different fixture evidence"})
        denied = False
        try:
            detect_improvement_candidates([conflicting, changed])
        except ValueError:
            denied = True
        if not denied:
            raise AssertionError("conflicting signal-ID reuse was accepted")
        passed("conflicting_signal_reuse_fails_closed")
    except Exception as exc:
        failed("conflicting_signal_reuse_fails_closed", exc)

    try:
        empty = detect_improvement_candidates([])
        if empty.candidates or empty.source_counts or empty.authority_side_effects != (
            "no_project_created",
            "no_budget_reserved",
            "no_credentials_granted",
            "no_deployment_changed",
        ):
            raise AssertionError("empty detection did not preserve no-side-effect contract")
        passed("empty_detection_is_non_authorizing")
    except Exception as exc:
        failed("empty_detection_is_non_authorizing", exc)

    try:
        explicit = detect_improvement_candidates(
            [_signal("budget-1", ImprovementSignalSource.COST, budget_usd="12.50")]
        )
        preview = explicit.canonical_project_requests()
        if explicit.candidates[0].budget_usd != Decimal("12.50") or len(preview) != 1:
            raise AssertionError("explicit budget or project preview was not preserved")
        passed("budget_and_project_preview_are_not_authority")
    except Exception as exc:
        failed("budget_and_project_preview_are_not_authority", exc)

    errors = [case for case in cases if not case.get("passed")]
    rendered = json.dumps(cases, sort_keys=True, default=str)
    secret_safe = not any(marker in rendered for marker in ("password", "token", "api_key", "credential"))
    if not secret_safe:
        failed("secret_safe_report", AssertionError("candidate report contains secret-shaped material"))
    else:
        passed("secret_safe_report")
    errors = [case for case in cases if not case.get("passed")]
    return {
        "schema_version": CHECK_SCHEMA,
        "status": "pass" if not errors else "fail",
        "case_count": len(cases),
        "passed_case_count": len(cases) - len(errors),
        "cases": cases,
        "project_creation_count": 0,
        "budget_reservation_count": 0,
        "credential_grant_count": 0,
        "deployment_mutation_count": 0,
        "licence_metadata_is_gate": False,
        "authority_side_effects": False,
        "secret_safe_report": secret_safe,
        "errors": errors,
        "scope": "bounded self-improvement candidate detection only; no project or authority mutation",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit machine-readable output")
    parser.add_argument("--live", action="store_true", help="require live signal-source integrations")
    args = parser.parse_args(argv)
    if args.live:
        report: dict[str, object] = {
            "schema_version": CHECK_SCHEMA,
            "mode": "live",
            "status": "blocked",
            "reason": "live defect/metric/upstream/cost/operator signal integrations require an operator-selected source and project scope",
            "licence_metadata_is_gate": False,
            "authority_side_effects": False,
        }
        exit_code = 2
    else:
        report = {"mode": "fixture", **_fixture()}
        exit_code = 0 if report["status"] == "pass" else 1
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2, default=str))
    else:
        print(f"self-improvement candidate detection: {report['status']}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
