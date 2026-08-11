"""Check deterministic evidence-policy scope precedence.

The fixture exercises project milestone, project, flow, company milestone,
company default, and manual fallback selections through the canonical core
resolver.  It is read-only: no project, flow, company, or evidence state is
loaded or mutated.  Resource licence/restriction values are metadata only and
are deliberately absent from the resolution inputs.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from mas_core.workflow import resolve_evidence_policy_selection

CHECK_SCHEMA = "aiat.evidence-policy-resolution-check.v1"


def _selection(policy_id: str) -> dict[str, Any]:
    return {"policy_id": policy_id, "version": "1.0", "requirements": {}}


def build_report() -> dict[str, Any]:
    cases: list[dict[str, Any]] = []

    def case(name: str, expected_source: str, expected_policy: Any, **scopes: Any) -> None:
        selected, source = resolve_evidence_policy_selection(**scopes)
        actual_policy = selected.get("policy_id") if isinstance(selected, dict) else selected
        cases.append(
            {
                "name": name,
                "expected_source": expected_source,
                "actual_source": source,
                "expected_policy": expected_policy,
                "actual_policy": actual_policy,
                "passed": source == expected_source and actual_policy == expected_policy,
            }
        )

    common = {
        "milestone": "implementation",
        "project_milestone_policies": {"implementation": _selection("project-milestone")},
        "project_selection": _selection("project"),
        "flow_selection": _selection("flow"),
        "company_milestone_policies": {"implementation": _selection("company-milestone")},
        "company_selection": _selection("company"),
    }
    case("project milestone wins", "project_milestone", "project-milestone", **common)
    case(
        "project wins over flow and company",
        "project",
        "project",
        **{**common, "project_milestone_policies": {}},
    )
    case(
        "flow wins over company",
        "flow",
        "flow",
        **{**common, "project_milestone_policies": {}, "project_selection": None},
    )
    case(
        "company milestone wins over company default",
        "company_milestone",
        "company-milestone",
        **{
            **common,
            "project_milestone_policies": {},
            "project_selection": None,
            "flow_selection": None,
        },
    )
    case(
        "company default is used when no override exists",
        "company",
        "company",
        **{
            **common,
            "project_milestone_policies": {},
            "project_selection": None,
            "flow_selection": None,
            "company_milestone_policies": {},
        },
    )
    case(
        "manual fallback is deterministic",
        "fallback",
        "manual",
        milestone="implementation",
    )
    case(
        "blank selections are ignored",
        "company",
        "company",
        milestone="implementation",
        project_selection=" ",
        flow_selection={},
        company_selection=_selection("company"),
    )

    errors = [case_row for case_row in cases if not case_row["passed"]]
    return {
        "schema_version": CHECK_SCHEMA,
        "status": "pass" if not errors else "fail",
        "case_count": len(cases),
        "cases": cases,
        "licence_metadata_is_gate": False,
        "mutation_performed": False,
        "live_resolution_status": "not_checked",
        "errors": errors,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument("--live", action="store_true", help="require a configured live policy resolver")
    args = parser.parse_args(argv)
    if args.live:
        report: dict[str, Any] = {
            "schema_version": CHECK_SCHEMA,
            "mode": "live",
            "status": "blocked",
            "reason": "live policy resolution requires an authenticated project/company API scenario",
            "mutation_performed": False,
        }
        exit_code = 2
    else:
        report = {"mode": "fixture", **build_report()}
        exit_code = 0 if report["status"] == "pass" else 1
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        print(f"evidence policy resolution: {report['status']}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
