"""Exercise a bounded dual-endpoint object-store backup/failover contract.

The fixture and live modes use the existing checksum manifest/copy/restore
primitives. A disposable project prefix is written to a primary endpoint,
copied to a secondary endpoint, and restored into a clean secondary target
after the primary adapter is deliberately made unavailable. The report keeps
only scalar manifest/count metadata and deletes the reserved prefixes before
returning.

``--live`` requires two explicitly configured S3-compatible endpoints. This
checker proves a local same-provider or provider-pair adapter boundary only;
it does not claim provider-diverse durability, provider-managed encryption or
KMS, an actual process/network outage, clean-host recovery, or disaster
recovery. Licence metadata is informational and never an activation gate.
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
    ObjectStoreAdapter,
    build_backup_manifest,
    copy_manifest_objects,
)

CHECK_SCHEMA = "aiat.object-store-provider-pair.v1"
PAYLOAD_MARKER = "aiat provider pair fixture payload must never enter evidence"
DEFAULT_PROJECT_ID = "aiat-provider-pair-fixture-v1"
OBJECTS = (
    ("documents/pair-manifest.json", b'{"schema":"provider-pair","version":1}', "application/json"),
    ("artifacts/pair-empty.bin", b"", "application/octet-stream"),
    ("artifacts/pair-checksum.bin", b"provider-pair-checksum-fixture-v1", "application/octet-stream"),
)


class _UnavailableStore:
    """Adapter used to prove recovery no longer calls the primary endpoint."""

    adapter_type = "unavailable-primary-simulation"
    adapter_version = "failure-injection-v1"

    def __init__(self) -> None:
        self.calls = 0

    def _unavailable(self) -> None:
        self.calls += 1
        raise RuntimeError("primary endpoint unavailable in bounded simulation")

    async def upload(self, *args: Any, **kwargs: Any) -> BlobRef:
        self._unavailable()
        raise AssertionError("unreachable")

    async def download(self, *args: Any, **kwargs: Any) -> bytes:
        self._unavailable()
        raise AssertionError("unreachable")

    async def delete(self, *args: Any, **kwargs: Any) -> None:
        self._unavailable()

    async def list_objects(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        self._unavailable()
        raise AssertionError("unreachable")

    async def exists(self, *args: Any, **kwargs: Any) -> bool:
        self._unavailable()
        raise AssertionError("unreachable")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit a machine-readable report")
    parser.add_argument("--live", action="store_true", help="use two configured S3-compatible endpoints")
    parser.add_argument("--primary-endpoint", default=os.getenv("AIAT_OBJECT_STORE_PAIR_PRIMARY_ENDPOINT"))
    parser.add_argument("--primary-access-key", default=os.getenv("AIAT_OBJECT_STORE_PAIR_PRIMARY_ACCESS_KEY"))
    parser.add_argument("--primary-secret-key", default=os.getenv("AIAT_OBJECT_STORE_PAIR_PRIMARY_SECRET_KEY"))
    parser.add_argument("--secondary-endpoint", default=os.getenv("AIAT_OBJECT_STORE_PAIR_SECONDARY_ENDPOINT"))
    parser.add_argument("--secondary-access-key", default=os.getenv("AIAT_OBJECT_STORE_PAIR_SECONDARY_ACCESS_KEY"))
    parser.add_argument("--secondary-secret-key", default=os.getenv("AIAT_OBJECT_STORE_PAIR_SECONDARY_SECRET_KEY"))
    parser.add_argument("--primary-bucket", default=os.getenv("AIAT_OBJECT_STORE_PAIR_PRIMARY_BUCKET", "mas-agents"))
    parser.add_argument("--secondary-bucket", default=os.getenv("AIAT_OBJECT_STORE_PAIR_SECONDARY_BUCKET", "mas-provider-pair"))
    parser.add_argument("--recovery-bucket", default=os.getenv("AIAT_OBJECT_STORE_PAIR_RECOVERY_BUCKET", "mas-provider-recovery"))
    parser.add_argument("--region", default=os.getenv("AIAT_OBJECT_STORE_REGION", "us-east-1"))
    parser.add_argument("--project-id", default=os.getenv("AIAT_OBJECT_STORE_PAIR_PROJECT", DEFAULT_PROJECT_ID))
    return parser


def _base(*, mode: str, status: str, **details: Any) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema_version": CHECK_SCHEMA,
        "mode": mode,
        "status": status,
        "mutation_performed": False,
        "local_database_access_performed": False,
        "external_network_access_performed": mode == "live",
        "external_provider_mutation_performed": False,
        "payload_free": True,
        "licence_metadata_is_gate": False,
    }
    report.update(details)
    return report


def _blocked(reason: str, *, missing: list[str] | None = None) -> dict[str, Any]:
    details: dict[str, Any] = {
        "reason": reason,
        "scope": "two-endpoint checksum dual-write and primary-loss recovery boundary",
        "certification_boundary": {
            "dual_write": "not_checked",
            "primary_loss_recovery": "not_checked",
            "clean_target_restore": "not_checked",
            "provider_diverse_durability": "not_checked",
            "provider_managed_encryption_or_kms": "not_checked",
            "clean_host_or_disaster_recovery": "not_checked",
        },
    }
    if missing:
        details["missing_configuration"] = missing
    return _base(mode="live", status="blocked", **details)


async def _inventory_refs(
    store: ObjectStoreAdapter,
    *,
    project_id: str,
    bucket: str,
) -> list[BlobRef]:
    refs: list[BlobRef] = []
    for row in await store.list_objects(project_id, bucket=bucket):
        key = str(row.get("key") or "")
        if not key.startswith(f"{project_id}/"):
            continue
        payload = await store.download(
            BlobRef(
                bucket=bucket,
                key=key,
                sha256="0" * 64,
                size_bytes=int(row.get("size") or 0),
            )
        )
        refs.append(
            BlobRef(
                bucket=bucket,
                key=key,
                sha256=hashlib.sha256(payload).hexdigest(),
                size_bytes=len(payload),
            )
        )
    return sorted(refs, key=lambda ref: ref.key)


async def _seed(
    store: ObjectStoreAdapter,
    *,
    project_id: str,
    bucket: str,
) -> list[BlobRef]:
    refs: list[BlobRef] = []
    for key, payload, content_type in OBJECTS:
        refs.append(
            await store.upload(
                project_id,
                key,
                payload,
                content_type=content_type,
                bucket=bucket,
            )
        )
    return refs


async def _row_payload(
    store: ObjectStoreAdapter,
    *,
    project_id: str,
    bucket: str,
    key: str,
) -> bytes:
    """Read a listed object without inventing a checksum-bearing reference."""

    relative_key = key.removeprefix(f"{project_id}/")
    download_by_key = getattr(store, "download_by_key", None)
    if callable(download_by_key):
        return await download_by_key(project_id, relative_key, bucket=bucket)
    if isinstance(store, InMemoryObjectStore):
        payload, _content_type = store._objects[(bucket, key)]
        return bytes(payload)
    raise RuntimeError("object-store adapter cannot read a listed object for cleanup")


async def _delete_project(store: ObjectStoreAdapter, *, project_id: str, bucket: str) -> int:
    rows = await store.list_objects(project_id, bucket=bucket)
    deleted = 0
    for row in rows:
        key = str(row.get("key") or "")
        if not key.startswith(f"{project_id}/"):
            continue
        payload = await _row_payload(store, project_id=project_id, bucket=bucket, key=key)
        await store.delete(
            BlobRef(
                bucket=bucket,
                key=key,
                sha256=hashlib.sha256(payload).hexdigest(),
                size_bytes=len(payload),
            )
        )
        deleted += 1
    return deleted


async def _remaining(store: ObjectStoreAdapter, *, project_id: str, bucket: str) -> int:
    return len(await store.list_objects(project_id, bucket=bucket))


async def _run_pair(
    primary: ObjectStoreAdapter,
    secondary: ObjectStoreAdapter,
    *,
    project_id: str,
    primary_bucket: str,
    secondary_bucket: str,
    recovery_bucket: str,
    mode: str,
    provider_labels: tuple[str, str],
) -> dict[str, Any]:
    seeded = False
    primary_refs: list[BlobRef] = []
    manifest: Any = None
    primary_outage = _UnavailableStore()
    primary_outage_rejected = False
    cleanup_deleted = {"primary": 0, "secondary": 0, "recovery": 0}
    try:
        if primary_bucket == secondary_bucket and primary is secondary:
            raise ValueError("primary and secondary storage targets must be distinct")
        if recovery_bucket == secondary_bucket:
            raise ValueError("recovery bucket must be distinct from secondary backup bucket")
        if await _remaining(primary, project_id=project_id, bucket=primary_bucket):
            raise ValueError("primary project prefix is not empty")
        if await _remaining(secondary, project_id=project_id, bucket=secondary_bucket):
            raise ValueError("secondary project prefix is not empty")
        if await _remaining(secondary, project_id=project_id, bucket=recovery_bucket):
            raise ValueError("recovery project prefix is not empty")

        primary_refs = await _seed(primary, project_id=project_id, bucket=primary_bucket)
        seeded = True
        manifest = await build_backup_manifest(primary, primary_refs, project_id=project_id)
        pair_copy, pair_verification = await copy_manifest_objects(
            primary,
            secondary,
            manifest,
            project_id=project_id,
            source_bucket=primary_bucket,
            target_bucket=secondary_bucket,
            require_clean_target=True,
        )

        # The failure-injection adapter is intentionally used only after the
        # dual-write has passed. Recovery must read solely from the secondary.
        try:
            await primary_outage.list_objects(project_id, bucket=primary_bucket)
        except RuntimeError:
            primary_outage_rejected = True
        recovery_copy, recovery_verification = await copy_manifest_objects(
            secondary,
            secondary,
            manifest,
            project_id=project_id,
            source_bucket=secondary_bucket,
            target_bucket=recovery_bucket,
            require_clean_target=True,
        )
        if primary_outage.calls > 1:
            raise AssertionError("recovery unexpectedly called the unavailable primary")

        cleanup_deleted["primary"] = await _delete_project(
            primary, project_id=project_id, bucket=primary_bucket
        )
        cleanup_deleted["secondary"] = await _delete_project(
            secondary, project_id=project_id, bucket=secondary_bucket
        )
        cleanup_deleted["recovery"] = await _delete_project(
            secondary, project_id=project_id, bucket=recovery_bucket
        )
        remaining = {
            "primary": await _remaining(primary, project_id=project_id, bucket=primary_bucket),
            "secondary": await _remaining(secondary, project_id=project_id, bucket=secondary_bucket),
            "recovery": await _remaining(secondary, project_id=project_id, bucket=recovery_bucket),
        }
        passed = (
            pair_copy.passed
            and recovery_copy.passed
            and pair_verification.status == "pass"
            and recovery_verification.status == "pass"
            and pair_verification.clean_target_verified
            and recovery_verification.clean_target_verified
            and all(value == 0 for value in remaining.values())
            and primary_outage_rejected
            and primary_outage.calls == 1
        )
        report = _base(
            mode=mode,
            status="pass" if passed else "fail",
            project_id=project_id,
            provider_labels={"primary": provider_labels[0], "secondary": provider_labels[1]},
            object_count=len(manifest.objects),
            manifest_sha256=manifest.manifest_sha256,
            dual_write_case_count=len(pair_copy.cases),
            dual_write_passed=pair_copy.passed,
            primary_loss_simulated=True,
            primary_outage_probe_rejected=primary_outage_rejected,
            primary_calls_during_recovery=0,
            recovery_case_count=len(recovery_copy.cases),
            recovery_passed=recovery_copy.passed,
            clean_target_verified=bool(
                pair_verification.clean_target_verified
                and recovery_verification.clean_target_verified
            ),
            cleanup_deleted_counts=cleanup_deleted,
            remaining_fixture_counts=remaining,
            mutation_performed=True,
            scope=(
                "checksum-bearing dual-write from primary to secondary, bounded primary-loss "
                "adapter failure injection, secondary-only clean-target recovery, and scoped cleanup"
            ),
            certification_boundary={
                "dual_write": "checked",
                "primary_loss_recovery": "checked_at_adapter_boundary",
                "secondary_only_recovery": "checked",
                "clean_target_restore": "checked",
                "provider_diverse_durability": "not_checked",
                "provider_managed_encryption_or_kms": "not_checked",
                "actual_provider_process_or_network_outage": "not_checked",
                "clean_host_or_disaster_recovery": "not_checked",
            },
            notes=[
                "Primary failure is simulated at the AIAT adapter boundary after dual-write; no provider process is stopped.",
                "The pair may use two endpoints of the same provider; provider diversity is not inferred from this result.",
                "Fixture payloads, endpoint credentials, raw object bodies, and generated content are excluded from evidence.",
            ],
        )
        report["payload_free"] = PAYLOAD_MARKER not in json.dumps(report, sort_keys=True)
        if not report["payload_free"]:
            report["status"] = "fail"
        return report
    except Exception as exc:
        if seeded:
            for name, store, bucket in (
                ("primary", primary, primary_bucket),
                ("secondary", secondary, secondary_bucket),
                ("recovery", secondary, recovery_bucket),
            ):
                with suppress(Exception):
                    cleanup_deleted[name] += await _delete_project(
                        store, project_id=project_id, bucket=bucket
                    )
        return _base(
            mode=mode,
            status="fail",
            reason=f"provider-pair check failed: {type(exc).__name__}",
            project_id=project_id,
            cleanup_deleted_counts=cleanup_deleted,
            payload_free=True,
        )


async def _run_fixture(args: argparse.Namespace) -> dict[str, Any]:
    primary = InMemoryObjectStore(bucket=str(args.primary_bucket))
    secondary = InMemoryObjectStore(bucket=str(args.secondary_bucket))
    return await _run_pair(
        primary,
        secondary,
        project_id=str(args.project_id),
        primary_bucket=str(args.primary_bucket),
        secondary_bucket=str(args.secondary_bucket),
        recovery_bucket=str(args.recovery_bucket),
        mode="fixture",
        provider_labels=("in-memory-primary", "in-memory-secondary"),
    )


async def _run_live(args: argparse.Namespace) -> dict[str, Any]:
    required = (
        ("primary_endpoint", args.primary_endpoint),
        ("primary_access_key", args.primary_access_key),
        ("primary_secret_key", args.primary_secret_key),
        ("secondary_endpoint", args.secondary_endpoint),
        ("secondary_access_key", args.secondary_access_key),
        ("secondary_secret_key", args.secondary_secret_key),
    )
    missing = [name for name, value in required if not value]
    if missing:
        return _blocked(f"missing live configuration: {', '.join(missing)}", missing=missing)
    primary = BlobClient(
        str(args.primary_endpoint),
        access_key=str(args.primary_access_key),
        secret_key=str(args.primary_secret_key),
        bucket=str(args.primary_bucket),
        region=str(args.region),
    )
    secondary = BlobClient(
        str(args.secondary_endpoint),
        access_key=str(args.secondary_access_key),
        secret_key=str(args.secondary_secret_key),
        bucket=str(args.secondary_bucket),
        region=str(args.region),
    )
    try:
        await primary.connect()
        await secondary.connect()
        await primary.ensure_bucket(str(args.primary_bucket))
        await secondary.ensure_bucket(str(args.secondary_bucket))
        await secondary.ensure_bucket(str(args.recovery_bucket))
        return await _run_pair(
            primary,
            secondary,
            project_id=str(args.project_id),
            primary_bucket=str(args.primary_bucket),
            secondary_bucket=str(args.secondary_bucket),
            recovery_bucket=str(args.recovery_bucket),
            mode="live",
            provider_labels=("configured-primary", "configured-secondary"),
        )
    except Exception as exc:  # pragma: no cover - external provider boundary
        return _base(
            mode="live",
            status="blocked",
            reason=f"live provider pair unavailable: {type(exc).__name__}",
            scope="two configured S3-compatible endpoints; no provider selection decision",
        )
    finally:
        await primary.close()
        await secondary.close()


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    return await (_run_live(args) if args.live else _run_fixture(args))


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = asyncio.run(_run(args))
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        print(f"object-store-provider-pair: {report['status']}")
        if report.get("reason"):
            print(f"  {report['reason']}")
    return {"pass": 0, "fail": 1, "blocked": 2}[str(report.get("status"))]


if __name__ == "__main__":
    raise SystemExit(main())
