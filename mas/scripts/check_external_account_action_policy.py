"""Check the external-account human-action policy contract.

The fixture exercises the identity service's real action catalogue and
category disposition logic without creating identities, accounts, sessions,
credentials, or provider state.  Unknown actions/categories must fail closed;
licence/restriction metadata is unrelated to this technical policy.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

IDENTITY_SERVICE_ROOT = Path(__file__).resolve().parents[1] / "apps" / "identity-service"
if str(IDENTITY_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(IDENTITY_SERVICE_ROOT))

from identity_service.external_accounts.service import (  # noqa: E402
    ExternalAccountPolicy,
    ExternalAccountPolicyError,
)

CHECK_SCHEMA = "aiat.external-account-action-policy-check.v1"


def build_report() -> dict[str, Any]:
    policy = ExternalAccountPolicy()
    catalog = policy.action_catalog()
    expected_actions = {
        "signup": {
            "risk": "category_sensitive",
            "approval_required": True,
            "approval_kind": "external_account",
            "disposition": "category_dependent",
        },
        "rotate_credentials": {
            "risk": "high",
            "approval_required": True,
            "approval_kind": "external_credential_rotation",
            "disposition": "approval_required",
        },
        "close": {
            "risk": "high",
            "approval_required": True,
            "approval_kind": "external_account_close",
            "disposition": "approval_required",
        },
        "suspend": {
            "risk": "safety",
            "approval_required": False,
            "approval_kind": None,
            "disposition": "immediate",
        },
        "browser_session": {
            "risk": "controlled",
            "approval_required": False,
            "approval_kind": None,
            "disposition": "governed_account_required",
        },
    }
    rows = {str(row.get("action")): row for row in catalog.get("actions", [])}
    action_cases = []
    for action, expected in expected_actions.items():
        row = rows.get(action, {})
        actual = {key: row.get(key) for key in expected}
        action_cases.append(
            {
                "action": action,
                "expected": expected,
                "actual": actual,
                "passed": actual == expected,
            }
        )

    category_cases = []
    for category, expected in (
        ("development_test", "allowed"),
        ("github_organization", "approval_required"),
        ("google", "approval_required"),
        ("microsoft", "approval_required"),
    ):
        try:
            actual = policy.disposition(category)
            passed = actual == expected
            error = None
        except Exception as exc:  # pragma: no cover - defensive fixture output
            actual = None
            passed = False
            error = f"{type(exc).__name__}: {exc}"
        category_cases.append(
            {
                "category": category,
                "expected": expected,
                "actual": actual,
                "error": error,
                "passed": passed,
            }
        )

    unknown_category_denied = False
    try:
        policy.disposition("unlisted-provider")
    except ExternalAccountPolicyError:
        unknown_category_denied = True

    unknown_action_denied = False
    try:
        policy.action_rule("delete_everything")
    except ExternalAccountPolicyError:
        unknown_action_denied = True

    errors = [
        *[case for case in action_cases if not case["passed"]],
        *[case for case in category_cases if not case["passed"]],
    ]
    if catalog.get("schema_version") != "aiat.external-account-action-policy.v1":
        errors.append({"case": "schema_version", "actual": catalog.get("schema_version")})
    if not unknown_category_denied:
        errors.append({"case": "unknown_category_denied"})
    if not unknown_action_denied:
        errors.append({"case": "unknown_action_denied"})

    return {
        "schema_version": CHECK_SCHEMA,
        "status": "pass" if not errors else "fail",
        "catalog_schema": catalog.get("schema_version"),
        "action_count": len(rows),
        "action_cases": action_cases,
        "category_cases": category_cases,
        "unknown_category_denied": unknown_category_denied,
        "unknown_action_denied": unknown_action_denied,
        "licence_metadata_is_gate": False,
        "mutation_performed": False,
        "live_provider_status": "not_checked",
        "errors": errors,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument("--live", action="store_true", help="require provider-specific live certification")
    args = parser.parse_args(argv)
    if args.live:
        report: dict[str, Any] = {
            "schema_version": CHECK_SCHEMA,
            "mode": "live",
            "status": "blocked",
            "reason": "provider-specific external-account certification requires a selected sandbox and outage/restore scenario",
            "mutation_performed": False,
        }
        exit_code = 2
    else:
        report = {"mode": "fixture", **build_report()}
        exit_code = 0 if report["status"] == "pass" else 1
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        print(f"external-account action policy: {report['status']}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
