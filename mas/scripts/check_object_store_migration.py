"""Run the deterministic object-store migration workflow fixture.

The fixture exercises checksum inventory, provider copy/read-back parity,
optional dual-write parity, explicit human-confirmed cutover, and explicit
human-confirmed rollback.  ``--live`` is intentionally fail-closed until a
provider-specific migration environment supplies routing, credentials,
retention, and rollback evidence; this command never mutates live providers.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from mas_core.memory import InMemoryObjectStore, ObjectStoreMigrationWorkflow


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit the full report as JSON")
    parser.add_argument("--live", action="store_true", help="report the guarded live boundary")
    return parser


async def _run_fixture() -> dict[str, Any]:
    source = InMemoryObjectStore(bucket="source")
    target = InMemoryObjectStore(bucket="target")
    project_id = "aiat-migration-fixture"
    refs = [
        await source.upload(project_id, "artifacts/alpha.txt", b"alpha", content_type="text/plain"),
        await source.upload(project_id, "artifacts/empty.bin", b""),
    ]
    workflow = ObjectStoreMigrationWorkflow.create(
        migration_id="aiat-migration-fixture-001",
        project_id=project_id,
        source_adapter_type="in-memory-fixture",
        target_adapter_type="in-memory-fixture",
        source_bucket="source",
        target_bucket="target",
        dual_write_required=True,
    )
    await workflow.inventory(source, refs, actor="fixture-system", actor_kind="system")
    await workflow.copy(source, target, actor="fixture-system", actor_kind="system")
    await workflow.dual_write(
        source,
        target,
        key="artifacts/live-write.txt",
        payload=b"dual-write",
        content_type="text/plain",
        actor="fixture-system",
        actor_kind="system",
    )
    workflow.cutover(actor="operator", actor_kind="human", confirm=True)
    workflow.rollback(
        actor="operator",
        actor_kind="human",
        confirm=True,
        reason="fixture rollback restores the source provider",
    )
    report = workflow.as_dict(include_timestamps=False)
    report.update(
        {
            "mode": "fixture",
            "status": "pass",
            "scope": "deterministic fixture; no live provider routing or data was mutated",
            "final_workflow_status": workflow.status,
        }
    )
    return report


def _live_boundary() -> dict[str, Any]:
    return {
        "schema_version": "aiat.object-store-migration.v1",
        "mode": "live",
        "status": "blocked",
        "reason": (
            "provider-specific migration environment is not configured; "
            "routing, credentials, retention, and rollback evidence are required"
        ),
        "scope": "no live provider routing or data was mutated",
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = _live_boundary() if args.live else asyncio.run(_run_fixture())
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    elif report["status"] == "blocked":
        print(f"object-store-migration: BLOCKED — {report['reason']}")
    else:
        print(
            "object-store-migration: "
            f"{report['schema_version']} {report['mode']} "
            f"PASS final={report['final_workflow_status']}"
        )
    return 2 if report["status"] == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
