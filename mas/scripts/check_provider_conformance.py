"""Run the provider-neutral PM/SCM conformance fixture.

The default mode runs the deterministic in-memory ``FakeProvider`` fixture.
``--live`` deliberately returns a blocked result because provider-specific
credentials, sandbox data, HTTP mocks, outage injection, and restoration
scope must be selected per adapter; it never fabricates a live certification.
Licence/restriction metadata is not part of the conformance predicate.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any
from uuid import uuid4

from mas_core.integrations.conformance import run_work_management_conformance
from mas_core.integrations.contracts import ProviderConnection
from mas_core.integrations.providers.fake import FakeProvider

CONFORMANCE_SCHEMA = "aiat.provider-conformance-runner.v1"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument("--live", action="store_true", help="require provider-specific live certification")
    parser.add_argument("--provider", default="fake", choices=("fake",))
    return parser


def _blocked(reason: str) -> dict[str, Any]:
    return {
        "schema_version": CONFORMANCE_SCHEMA,
        "mode": "live",
        "status": "blocked",
        "reason": reason,
        "provider": "provider-specific",
        "fixture_report": None,
        "scope": "provider-specific live HTTP/outage/restore certification",
    }


async def _fixture_report() -> dict[str, Any]:
    connection = ProviderConnection(
        provider_kind="fake",
        display_name="AIAT disposable conformance fixture",
        base_url="https://provider.fixture.invalid",
        credential_ref="fixture-credential",
        config={"fixture_id": str(uuid4())},
    )
    report = await run_work_management_conformance(FakeProvider(), connection)
    return {
        "schema_version": CONFORMANCE_SCHEMA,
        "mode": "fixture",
        "status": "pass" if report.passed else "fail",
        "provider": "fake",
        "fixture_report": report.as_dict(),
        "scope": "deterministic in-memory provider contract only",
    }


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    if args.live:
        return _blocked(
            "live provider certification requires an adapter-specific sandbox connection, "
            "mock HTTP contract, outage injection, and restore plan"
        )
    return await _fixture_report()


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = asyncio.run(_run(args))
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        fixture = report.get("fixture_report") or {}
        counts = fixture.get("counts") or {}
        print(
            "provider conformance: "
            f"{report['status']} mode={report['mode']} "
            f"PASS={counts.get('PASS', 0)} FAIL={counts.get('FAIL', 0)} SKIP={counts.get('SKIP', 0)}"
        )
        if report.get("reason"):
            print(f"reason: {report['reason']}")
    return 2 if report["status"] == "blocked" else (1 if report["status"] == "fail" else 0)


if __name__ == "__main__":
    sys.exit(main())
