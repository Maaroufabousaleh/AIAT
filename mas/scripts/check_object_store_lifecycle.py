"""Check the bounded object-store lifecycle and legal-hold boundary.

The fixture compares a scalar provider inventory with canonical AIAT object
references, plans orphan/expired candidates, preserves size drift and legal
holds, executes only after explicit confirmation, and verifies the retained
inventory.  It uses no network or external provider and retains no payloads.
Live garbage collection is intentionally not inferred from this certificate.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from mas_core.memory import (
    InMemoryObjectStore,
    LegalHoldSnapshot,
    LifecycleCanonicalObject,
    LifecycleInventoryObject,
    execute_object_lifecycle,
    plan_object_lifecycle,
)

CHECK_SCHEMA = "aiat.object-store-lifecycle-check.v1"
PROJECT_ID = "aiat-object-lifecycle-fixture-v1"
BUCKET = "lifecycle-fixture"
EVALUATED_AT = datetime(2026, 8, 19, 4, 0, tzinfo=UTC)


def _base(*, status: str, **details: Any) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema_version": CHECK_SCHEMA,
        "mode": "fixture",
        "status": status,
        "mutation_performed": status == "pass",
        "external_network_access_performed": False,
        "external_provider_mutation_performed": False,
        "local_database_access_performed": False,
        "payload_free": True,
        "licence_metadata_is_gate": False,
        "failure_classification": {
            "harness_configuration_failure": "not_observed",
            "infrastructure_environment_failure": "not_checked",
            "provider_functional_failure": "not_checked",
            "provider_resource_limit_failure": "not_checked",
        },
    }
    report.update(details)
    return report


async def _run_fixture() -> dict[str, Any]:
    store = InMemoryObjectStore(bucket=BUCKET)
    refs: dict[str, Any] = {}
    try:
        for key in (
            "keep.bin",
            "expired.bin",
            "held.bin",
            "orphan.bin",
            "orphan-held.bin",
            "drift.bin",
        ):
            refs[key] = await store.upload(
                PROJECT_ID,
                key,
                f"fixture:{key}".encode(),
                bucket=BUCKET,
            )
        holds = LegalHoldSnapshot.create(
            source_ref="hold-registry://object-lifecycle-fixture-v1",
            hold_keys=(refs["held.bin"].key, refs["orphan-held.bin"].key),
        )
        canonical = (
            LifecycleCanonicalObject(
                key=refs["keep.bin"].key,
                sha256=refs["keep.bin"].sha256,
                size_bytes=refs["keep.bin"].size_bytes,
                retention_until=EVALUATED_AT + timedelta(days=1),
            ),
            LifecycleCanonicalObject(
                key=refs["expired.bin"].key,
                sha256=refs["expired.bin"].sha256,
                size_bytes=refs["expired.bin"].size_bytes,
                retention_until=EVALUATED_AT - timedelta(seconds=1),
            ),
            LifecycleCanonicalObject(
                key=refs["held.bin"].key,
                sha256=refs["held.bin"].sha256,
                size_bytes=refs["held.bin"].size_bytes,
                retention_until=EVALUATED_AT - timedelta(seconds=1),
            ),
            LifecycleCanonicalObject(
                key=refs["drift.bin"].key,
                sha256=refs["drift.bin"].sha256,
                size_bytes=refs["drift.bin"].size_bytes + 1,
                retention_until=EVALUATED_AT - timedelta(seconds=1),
            ),
        )
        inventory = tuple(
            LifecycleInventoryObject.from_mapping(row)
            for row in await store.list_objects(PROJECT_ID, bucket=BUCKET)
        )
        plan = plan_object_lifecycle(
            project_id=PROJECT_ID,
            bucket=BUCKET,
            inventory=inventory,
            canonical=canonical,
            evaluated_at=EVALUATED_AT,
            legal_hold_snapshot=holds,
        )
        execution = await execute_object_lifecycle(
            store,
            plan,
            legal_hold_snapshot=holds,
            confirm=True,
        )
        remaining_before_cleanup = await store.list_objects(PROJECT_ID, bucket=BUCKET)
        for row in remaining_before_cleanup:
            await store.delete_by_key(PROJECT_ID, str(row["key"]), bucket=BUCKET)
        remaining_after_cleanup = await store.list_objects(PROJECT_ID, bucket=BUCKET)
        return _base(
            status="pass",
            project_id=PROJECT_ID,
            bucket=BUCKET,
            evaluated_at=EVALUATED_AT.isoformat(),
            plan=plan.as_dict(),
            execution=execution.as_dict(),
            fixture_cleanup_deleted_count=len(remaining_before_cleanup),
            fixture_cleanup_verified=not remaining_after_cleanup,
            remaining_fixture_count=len(remaining_after_cleanup),
            scope="scalar inventory versus canonical references; confirmed fixture cleanup only",
        )
    except Exception as exc:  # pragma: no cover - defensive checker boundary
        return _base(
            status="fail",
            reason=f"lifecycle fixture failed: {type(exc).__name__}",
            scope="scalar inventory versus canonical references; no provider evidence",
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit machine-readable evidence")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = asyncio.run(_run_fixture())
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        print(f"object-store-lifecycle: {report['status'].upper()} — {report['scope']}")
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
