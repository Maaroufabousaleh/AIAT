"""Run the governed object-store migration workflow fixture or rehearsal.

Fixture mode exercises checksum inventory, provider copy/read-back parity,
optional dual-write parity, explicit human-confirmed cutover, and explicit
human-confirmed rollback.  ``--live`` is a deliberately bounded rehearsal:
it requires two explicit S3-compatible endpoints, a reserved fixture project,
``--seed-fixture``, and both human confirmation flags.  It mutates only those
reserved prefixes and records the workflow decision; it never changes AIAT
deployment routing, retention authority, or a production provider selection.
Licence metadata is informational and never a transition gate.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from contextlib import suppress
from typing import Any

from mas_core.memory import (
    BlobClient,
    BlobRef,
    InMemoryObjectStore,
    ObjectStoreMigrationError,
    ObjectStoreMigrationWorkflow,
)

MIGRATION_PAYLOAD_MARKER = "aiat live migration rehearsal payload must never enter evidence"
LIVE_PROJECT_PREFIX = "aiat-migration-live-"
LIVE_FIXTURE_OBJECTS = (
    ("artifacts/migration-alpha.txt", b"migration-alpha", "text/plain"),
    ("artifacts/migration-empty.bin", b"", "application/octet-stream"),
    ("manifests/migration.json", b'{"schema":"migration-rehearsal","version":1}', "application/json"),
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit the full report as JSON")
    parser.add_argument("--live", action="store_true", help="report the guarded live boundary")
    parser.add_argument("--source-endpoint", default=os.getenv("AIAT_OBJECT_STORE_MIGRATION_SOURCE_ENDPOINT"))
    parser.add_argument("--source-access-key", default=os.getenv("AIAT_OBJECT_STORE_MIGRATION_SOURCE_ACCESS_KEY"))
    parser.add_argument("--source-secret-key", default=os.getenv("AIAT_OBJECT_STORE_MIGRATION_SOURCE_SECRET_KEY"))
    parser.add_argument("--target-endpoint", default=os.getenv("AIAT_OBJECT_STORE_MIGRATION_TARGET_ENDPOINT"))
    parser.add_argument("--target-access-key", default=os.getenv("AIAT_OBJECT_STORE_MIGRATION_TARGET_ACCESS_KEY"))
    parser.add_argument("--target-secret-key", default=os.getenv("AIAT_OBJECT_STORE_MIGRATION_TARGET_SECRET_KEY"))
    parser.add_argument("--source-bucket", default=os.getenv("AIAT_OBJECT_STORE_MIGRATION_SOURCE_BUCKET", "mas-agents"))
    parser.add_argument("--target-bucket", default=os.getenv("AIAT_OBJECT_STORE_MIGRATION_TARGET_BUCKET", "mas-migration-live"))
    parser.add_argument("--region", default=os.getenv("AIAT_OBJECT_STORE_REGION", "us-east-1"))
    parser.add_argument(
        "--project-id",
        default=os.getenv("AIAT_OBJECT_STORE_MIGRATION_PROJECT", "aiat-migration-live-fixture"),
    )
    parser.add_argument("--source-label", default=os.getenv("AIAT_OBJECT_STORE_MIGRATION_SOURCE_LABEL", "configured-source"))
    parser.add_argument("--target-label", default=os.getenv("AIAT_OBJECT_STORE_MIGRATION_TARGET_LABEL", "configured-target"))
    parser.add_argument(
        "--seed-fixture",
        action="store_true",
        help="write and later clean only the reserved live fixture project",
    )
    parser.add_argument(
        "--confirm-cutover",
        action="store_true",
        help="explicitly confirm the bounded workflow cutover record",
    )
    parser.add_argument(
        "--confirm-rollback",
        action="store_true",
        help="explicitly confirm the bounded workflow rollback record",
    )
    return parser


def _blocked_live(reason: str, *, missing: list[str] | None = None) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema_version": "aiat.object-store-migration.v1",
        "mode": "live",
        "status": "blocked",
        "reason": reason,
        "scope": "reserved-project migration rehearsal; no deployment routing or production cutover",
        "deployment_routing_mutated": False,
        "licence_metadata_is_gate": False,
    }
    if missing:
        report["missing_configuration"] = missing
    return report


async def _inventory_refs(
    client: BlobClient,
    *,
    project_id: str,
    bucket: str,
) -> list[BlobRef]:
    refs: list[BlobRef] = []
    for row in await client.list_objects(project_id, bucket=bucket):
        key = str(row.get("key") or "")
        if not key.startswith(f"{project_id}/"):
            continue
        relative_key = key.removeprefix(f"{project_id}/")
        payload = await client.download_by_key(project_id, relative_key, bucket=bucket)
        refs.append(
            BlobRef(
                bucket=bucket,
                key=key,
                sha256=hashlib.sha256(payload).hexdigest(),
                size_bytes=len(payload),
            )
        )
    return sorted(refs, key=lambda ref: ref.key)


async def _seed_fixture(client: BlobClient, *, project_id: str, bucket: str) -> list[BlobRef]:
    refs: list[BlobRef] = []
    for key, payload, content_type in LIVE_FIXTURE_OBJECTS:
        refs.append(
            await client.upload(
                project_id,
                key,
                payload,
                content_type=content_type,
                bucket=bucket,
            )
        )
    return refs


async def _delete_project(client: BlobClient, *, project_id: str, bucket: str) -> int:
    deleted = 0
    for row in await client.list_objects(project_id, bucket=bucket):
        key = str(row.get("key") or "")
        if not key.startswith(f"{project_id}/"):
            continue
        relative_key = key.removeprefix(f"{project_id}/")
        payload = await client.download_by_key(project_id, relative_key, bucket=bucket)
        await client.delete(
            BlobRef(
                bucket=bucket,
                key=key,
                sha256=hashlib.sha256(payload).hexdigest(),
                size_bytes=len(payload),
            )
        )
        deleted += 1
    return deleted


async def _remaining_project(client: BlobClient, *, project_id: str, bucket: str) -> int:
    return sum(
        1
        for row in await client.list_objects(project_id, bucket=bucket)
        if str(row.get("key") or "").startswith(f"{project_id}/")
    )


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


async def _run_live(args: argparse.Namespace) -> dict[str, Any]:
    required = (
        ("source_endpoint", args.source_endpoint),
        ("source_access_key", args.source_access_key),
        ("source_secret_key", args.source_secret_key),
        ("target_endpoint", args.target_endpoint),
        ("target_access_key", args.target_access_key),
        ("target_secret_key", args.target_secret_key),
    )
    missing = [name for name, value in required if not value]
    if missing:
        return _blocked_live(
            "provider-specific migration environment is not configured; "
            f"missing live configuration: {', '.join(missing)}",
            missing=missing,
        )
    if not args.seed_fixture:
        return _blocked_live("--seed-fixture is required for the bounded live rehearsal")
    if not args.confirm_cutover or not args.confirm_rollback:
        return _blocked_live(
            "--confirm-cutover and --confirm-rollback are required; no workflow decision was recorded"
        )
    project_id = str(args.project_id)
    if not project_id.startswith(LIVE_PROJECT_PREFIX):
        return _blocked_live(
            f"project-id must use the reserved {LIVE_PROJECT_PREFIX!r} prefix for live cleanup"
        )
    if str(args.source_bucket) == str(args.target_bucket):
        return _blocked_live("source and target buckets must be distinct")

    source = BlobClient(
        str(args.source_endpoint),
        access_key=str(args.source_access_key),
        secret_key=str(args.source_secret_key),
        bucket=str(args.source_bucket),
        region=str(args.region),
    )
    target = BlobClient(
        str(args.target_endpoint),
        access_key=str(args.target_access_key),
        secret_key=str(args.target_secret_key),
        bucket=str(args.target_bucket),
        region=str(args.region),
    )
    seeded = False
    cleanup_deleted = {"source": 0, "target": 0}
    try:
        await source.connect()
        await target.connect()
        await source.ensure_bucket(str(args.source_bucket))
        await target.ensure_bucket(str(args.target_bucket))
        if await _remaining_project(source, project_id=project_id, bucket=str(args.source_bucket)):
            return _blocked_live("reserved source project prefix is not empty")
        if await _remaining_project(target, project_id=project_id, bucket=str(args.target_bucket)):
            return _blocked_live("reserved target project prefix is not empty")

        await _seed_fixture(source, project_id=project_id, bucket=str(args.source_bucket))
        seeded = True
        refs = await _inventory_refs(source, project_id=project_id, bucket=str(args.source_bucket))
        if not refs:
            raise ObjectStoreMigrationError("seeded source inventory is empty")
        workflow = ObjectStoreMigrationWorkflow.create(
            migration_id=f"{project_id}-workflow",
            project_id=project_id,
            source_adapter_type=str(getattr(source, "adapter_type", "s3-compatible")),
            target_adapter_type=str(getattr(target, "adapter_type", "s3-compatible")),
            source_bucket=str(args.source_bucket),
            target_bucket=str(args.target_bucket),
            dual_write_required=True,
        )
        await workflow.inventory(source, refs, actor="migration-checker", actor_kind="system")
        await workflow.copy(source, target, actor="migration-checker", actor_kind="system")
        await workflow.dual_write(
            source,
            target,
            key="artifacts/migration-dual-write.txt",
            payload=b"migration-dual-write",
            content_type="text/plain",
            actor="migration-checker",
            actor_kind="system",
        )
        workflow.cutover(actor="operator", actor_kind="human", confirm=True)
        workflow.rollback(
            actor="operator",
            actor_kind="human",
            confirm=True,
            reason="bounded rehearsal restores source authority after cutover evidence",
        )
        if workflow.status != "ROLLED_BACK":
            raise ObjectStoreMigrationError("workflow did not finish in ROLLED_BACK")
        cleanup_deleted["source"] = await _delete_project(
            source, project_id=project_id, bucket=str(args.source_bucket)
        )
        cleanup_deleted["target"] = await _delete_project(
            target, project_id=project_id, bucket=str(args.target_bucket)
        )
        remaining = {
            "source": await _remaining_project(
                source, project_id=project_id, bucket=str(args.source_bucket)
            ),
            "target": await _remaining_project(
                target, project_id=project_id, bucket=str(args.target_bucket)
            ),
        }
        report = workflow.as_dict()
        report.update(
            {
                "mode": "live",
                "status": "pass",
                "final_workflow_status": workflow.status,
                "provider_topology": {
                    "source": str(args.source_label),
                    "target": str(args.target_label),
                    "provider_diversity": "operator-observed labels; no durability inference",
                },
                "operator_confirmed_cutover": True,
                "operator_confirmed_rollback": True,
                "deployment_routing_mutated": False,
                "retention_parity": "not_checked",
                "cleanup_deleted_counts": cleanup_deleted,
                "remaining_fixture_counts": remaining,
                "mutation_performed": True,
                "external_network_access_performed": True,
                "external_provider_mutation_performed": True,
                "payload_free": MIGRATION_PAYLOAD_MARKER not in json.dumps(report, sort_keys=True),
                "licence_metadata_is_gate": False,
                "scope": (
                    "reserved-project inventory, checksum copy/read-back, explicit dual-write, "
                    "human-confirmed workflow cutover/rollback record, and scoped cleanup"
                ),
                "certification_boundary": {
                    "inventory": "checked",
                    "copy_and_clean_target_parity": "checked",
                    "dual_write": "checked",
                    "human_confirmed_cutover_record": "checked",
                    "human_confirmed_rollback_record": "checked",
                    "deployment_routing_mutation": "not_checked",
                    "retention_policy_parity": "not_checked",
                    "actual_provider_process_or_network_outage": "not_checked",
                    "clean_host_or_disaster_recovery": "not_checked",
                },
                "notes": [
                    "Cutover and rollback are recorded in the AIAT-owned workflow evidence only; deployment routing was not changed.",
                    "The source and target are disposable S3-compatible endpoints and all reserved objects were removed after verification.",
                    "No endpoint URL, credential, object body, or generated content is retained.",
                    "Licence metadata is informational only and is not an activation, execution, or release gate.",
                ],
            }
        )
        if not all(value == 0 for value in remaining.values()) or not report["payload_free"]:
            report["status"] = "fail"
        return report
    except ObjectStoreMigrationError as exc:
        return {
            "schema_version": "aiat.object-store-migration.v1",
            "mode": "live",
            "status": "fail",
            "reason": f"migration workflow failed: {type(exc).__name__}",
            "payload_free": True,
            "licence_metadata_is_gate": False,
        }
    except Exception as exc:  # pragma: no cover - external provider boundary
        return _blocked_live(f"live provider unavailable: {type(exc).__name__}")
    finally:
        if seeded:
            for name, client, bucket in (
                ("source", source, str(args.source_bucket)),
                ("target", target, str(args.target_bucket)),
            ):
                if cleanup_deleted[name] == 0:
                    with suppress(Exception):
                        cleanup_deleted[name] = await _delete_project(
                            client, project_id=project_id, bucket=bucket
                        )
        await source.close()
        await target.close()


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = asyncio.run(_run_live(args)) if args.live else asyncio.run(_run_fixture())
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    elif report["status"] == "blocked":
        print(f"object-store-migration: BLOCKED — {report['reason']}")
    else:
        print(
            "object-store-migration: "
            f"{report['schema_version']} {report['mode']} "
            f"{str(report['status']).upper()} final={report.get('final_workflow_status', 'n/a')}"
        )
    return {"pass": 0, "fail": 1, "blocked": 2}[str(report["status"])]


if __name__ == "__main__":
    raise SystemExit(main())
