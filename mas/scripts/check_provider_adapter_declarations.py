"""Reconcile built-in provider capability declarations without network access.

The check instantiates the real YouTrack and GitHub adapters, exercises their
declared capability profiles and bounded identifier helpers, and records a
stable readiness report.  It deliberately does not call provider HTTP APIs,
create or mutate provider state, or use licence/restriction metadata as a
predicate.  Provider-specific mock/live conformance remains a separate gate.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any
from uuid import UUID

from mas_core.integrations.contracts import ProviderConnection
from mas_core.integrations.providers.github import GitHubProvider
from mas_core.integrations.providers.youtrack import YouTrackProvider

CHECK_SCHEMA = "aiat.provider-adapter-declarations.v1"
FIXTURE_ID = UUID("00000000-0000-4000-a000-000000000801")

CAPABILITY_KEYS = (
    "provider_kind",
    "adapter_version",
    "work_management",
    "source_control",
    "projects",
    "iterations",
    "work_items",
    "comments",
    "links",
    "repositories",
    "pull_requests",
    "checks",
    "webhooks",
    "incremental_sync",
    "supported_fields",
)

EXPECTED_GITHUB: dict[str, dict[str, Any]] = {
    "pm": {
        "work_management": True,
        "source_control": True,
        "projects": False,
        "iterations": False,
        "work_items": True,
        "comments": True,
        "links": False,
        "repositories": True,
        "pull_requests": False,
        "checks": False,
        "webhooks": True,
        "incremental_sync": True,
    },
    "delivery": {
        "work_management": True,
        "source_control": True,
        "projects": False,
        "iterations": False,
        "work_items": True,
        "comments": True,
        "links": False,
        "repositories": True,
        "pull_requests": True,
        "checks": False,
        "webhooks": True,
        "incremental_sync": True,
    },
    "checks": {
        "work_management": True,
        "source_control": True,
        "projects": False,
        "iterations": False,
        "work_items": True,
        "comments": True,
        "links": False,
        "repositories": True,
        "pull_requests": True,
        "checks": True,
        "webhooks": True,
        "incremental_sync": True,
    },
}
EXPECTED_FIELDS = {"title", "description", "status", "priority", "labels"}
EXPECTED_YOUTRACK = {
    "work_management": True,
    "source_control": False,
    "projects": True,
    "iterations": True,
    "work_items": True,
    "comments": True,
    "links": True,
    "repositories": False,
    "pull_requests": False,
    "checks": False,
    "webhooks": True,
    "incremental_sync": True,
}
EXPECTED_YOUTRACK_FIELDS = {
    "title",
    "description",
    "status",
    "priority",
    "sprint_id",
    "assigned_user",
}


def _connection(provider_kind: str, *, profile: str = "pm") -> ProviderConnection:
    config: dict[str, Any] = {}
    if provider_kind == "github":
        config["repository"] = "acme/aiat-fixture"
    if provider_kind == "youtrack":
        config["project_id"] = "AIAT"
    return ProviderConnection(
        id=FIXTURE_ID,
        provider_kind=provider_kind,
        display_name=f"AIAT {provider_kind} declaration fixture",
        base_url="https://provider.fixture.invalid",
        credential_ref="fixture-credential-ref",
        capability_profile=profile,
        config=config,
    )


def _capability_dict(capabilities: Any) -> dict[str, Any]:
    row = capabilities.model_dump(mode="json")
    row["supported_fields"] = sorted(str(value) for value in row.get("supported_fields") or [])
    return {key: row.get(key) for key in CAPABILITY_KEYS}


def _error_case(name: str, callback: Any) -> dict[str, Any]:
    try:
        callback()
    except ValueError:
        return {"case": name, "passed": True, "raised": "ValueError"}
    except Exception as exc:  # pragma: no cover - defensive report path
        return {"case": name, "passed": False, "raised": type(exc).__name__}
    return {"case": name, "passed": False, "raised": None}


async def _build_report() -> dict[str, Any]:
    github = GitHubProvider()
    youtrack = YouTrackProvider()
    errors: list[dict[str, Any] | str] = []
    capability_rows: list[dict[str, Any]] = []

    for profile, expected_flags in EXPECTED_GITHUB.items():
        connection = _connection("github", profile=profile)
        actual = _capability_dict(await github.capabilities(connection))
        expected = {
            "provider_kind": "github",
            "adapter_version": "1",
            **expected_flags,
            "supported_fields": sorted(EXPECTED_FIELDS),
        }
        passed = actual == expected
        capability_rows.append(
            {"provider": "github", "profile": profile, "expected": expected, "actual": actual, "passed": passed}
        )
        if not passed:
            errors.append({"case": f"github:{profile}:capabilities", "expected": expected, "actual": actual})

    youtrack_connection = _connection("youtrack")
    actual_youtrack = _capability_dict(await youtrack.capabilities(youtrack_connection))
    expected_youtrack = {
        "provider_kind": "youtrack",
        "adapter_version": "1",
        **EXPECTED_YOUTRACK,
        "supported_fields": sorted(EXPECTED_YOUTRACK_FIELDS),
    }
    passed = actual_youtrack == expected_youtrack
    capability_rows.append(
        {
            "provider": "youtrack",
            "profile": "pm",
            "expected": expected_youtrack,
            "actual": actual_youtrack,
            "passed": passed,
        }
    )
    if not passed:
        errors.append({"case": "youtrack:pm:capabilities", "expected": expected_youtrack, "actual": actual_youtrack})

    readiness_cases = [
        {"case": "github:adapter_identity", "passed": github.kind == "github" and github.adapter_version == "1"},
        {"case": "youtrack:adapter_identity", "passed": youtrack.kind == "youtrack" and youtrack.adapter_version == "1"},
        {"case": "github:repository_scope", "passed": github._repository(_connection("github")) == "acme/aiat-fixture"},
        _error_case(
            "github:repository_path_traversal_denied",
            lambda: GitHubProvider._repository(_connection("github", profile="pm").model_copy(update={"config": {"repository": "acme/../other"}})),
        ),
        _error_case(
            "github:unsafe_ref_denied",
            lambda: GitHubProvider._safe_git_ref("refs/heads/../../main", field="branch"),
        ),
        _error_case(
            "github:unsafe_identifier_denied",
            lambda: GitHubProvider._safe_identifier("../issue", field="external_id"),
        ),
        _error_case(
            "youtrack:unsafe_segment_denied",
            lambda: YouTrackProvider._safe_segment("../issue", field="external_id"),
        ),
    ]
    for case in readiness_cases:
        if not case["passed"]:
            errors.append(case)

    return {
        "schema_version": CHECK_SCHEMA,
        "status": "pass" if not errors else "fail",
        "provider_count": 2,
        "capability_profile_count": len(capability_rows),
        "capability_rows": capability_rows,
        "readiness_cases": readiness_cases,
        "network_access_performed": False,
        "mutation_performed": False,
        "provider_http_calls": 0,
        "licence_metadata_is_gate": False,
        "errors": errors,
        "scope": "real built-in adapter declarations and bounded identifier helpers only",
    }


def build_report() -> dict[str, Any]:
    return asyncio.run(_build_report())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit machine-readable output")
    parser.add_argument("--live", action="store_true", help="require provider-specific certification")
    args = parser.parse_args(argv)
    if args.live:
        report: dict[str, Any] = {
            "schema_version": CHECK_SCHEMA,
            "mode": "live",
            "status": "blocked",
            "reason": "provider-specific HTTP, sandbox, outage, and restore evidence requires a selected account",
            "network_access_performed": False,
            "mutation_performed": False,
            "licence_metadata_is_gate": False,
        }
        exit_code = 2
    else:
        report = {"mode": "fixture", **build_report()}
        exit_code = 0 if report["status"] == "pass" else 1
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        print(f"provider adapter declarations: {report['status']}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
