"""Run checksum-verified object-copy/parity checks.

Without ``--live`` the command uses deterministic in-memory adapters. With
``--live`` it inventories a project-scoped prefix from one S3-compatible
source, computes explicit :class:`BlobRef` checksums, and copies/parity-checks
those objects into a second configured provider. The live command never
deletes source data or performs a cutover. Missing configuration or an
unavailable provider returns exit code 2 with a machine-readable ``blocked``
result.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from typing import Any

from mas_core.memory import BlobClient, BlobRef, InMemoryObjectStore, verify_and_copy_blobs


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit the full parity report as JSON")
    parser.add_argument("--live", action="store_true", help="copy/parity-check between configured S3-compatible providers")
    parser.add_argument("--source-endpoint", default=os.getenv("AIAT_OBJECT_STORE_SOURCE_ENDPOINT"))
    parser.add_argument("--source-access-key", default=os.getenv("AIAT_OBJECT_STORE_SOURCE_ACCESS_KEY"))
    parser.add_argument("--source-secret-key", default=os.getenv("AIAT_OBJECT_STORE_SOURCE_SECRET_KEY"))
    parser.add_argument("--target-endpoint", default=os.getenv("AIAT_OBJECT_STORE_TARGET_ENDPOINT"))
    parser.add_argument("--target-access-key", default=os.getenv("AIAT_OBJECT_STORE_TARGET_ACCESS_KEY"))
    parser.add_argument("--target-secret-key", default=os.getenv("AIAT_OBJECT_STORE_TARGET_SECRET_KEY"))
    parser.add_argument("--source-bucket", default=os.getenv("AIAT_OBJECT_STORE_SOURCE_BUCKET", "mas-agents"))
    parser.add_argument("--target-bucket", default=os.getenv("AIAT_OBJECT_STORE_TARGET_BUCKET", "mas-agents"))
    parser.add_argument("--region", default=os.getenv("AIAT_OBJECT_STORE_REGION", "us-east-1"))
    parser.add_argument("--project-id", default=os.getenv("AIAT_OBJECT_STORE_COPY_PROJECT", "aiat-copy-live"))
    parser.add_argument("--prefix", default=os.getenv("AIAT_OBJECT_STORE_COPY_PREFIX", ""))
    return parser


async def _run_fixture() -> dict[str, Any]:
    source = InMemoryObjectStore(bucket="source")
    target = InMemoryObjectStore(bucket="backup")
    project_id = "aiat-copy-fixture"
    refs = [
        await source.upload(project_id, "artifacts/alpha.txt", b"alpha", content_type="text/plain"),
        await source.upload(project_id, "artifacts/empty.bin", b""),
    ]
    report = await verify_and_copy_blobs(
        source,
        target,
        refs,
        project_id=project_id,
        target_bucket="backup",
    )
    return report.as_dict()


async def _inventory_refs(
    client: BlobClient,
    *,
    project_id: str,
    bucket: str,
    prefix: str,
) -> list[BlobRef]:
    """Build checksum-bearing refs without trusting provider metadata."""

    refs: list[BlobRef] = []
    for row in await client.list_objects(project_id, prefix=prefix, bucket=bucket):
        full_key = str(row["key"])
        project_prefix = f"{project_id}/"
        if not full_key.startswith(project_prefix):
            continue
        relative_key = full_key.removeprefix(project_prefix)
        payload = await client.download_by_key(project_id, relative_key, bucket=bucket)
        refs.append(
            BlobRef(
                bucket=bucket,
                key=full_key,
                sha256=hashlib.sha256(payload).hexdigest(),
                size_bytes=len(payload),
            )
        )
    return refs


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
        return {
            "schema_version": "aiat.object-store-copy.v1",
            "mode": "live",
            "status": "blocked",
            "reason": f"missing live configuration: {', '.join(missing)}",
        }

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
    try:
        await source.connect()
        await target.connect()
        refs = await _inventory_refs(
            source,
            project_id=str(args.project_id),
            bucket=str(args.source_bucket),
            prefix=str(args.prefix),
        )
        if not refs:
            return {
                "schema_version": "aiat.object-store-copy.v1",
                "mode": "live",
                "status": "blocked",
                "project_id": str(args.project_id),
                "reason": "source inventory is empty; refusing to report a no-op migration as parity",
            }
        report = await verify_and_copy_blobs(
            source,
            target,
            refs,
            project_id=str(args.project_id),
            target_bucket=str(args.target_bucket),
        )
        result = report.as_dict()
        result.update(
            {
                "mode": "live",
                "source_endpoint": str(args.source_endpoint),
                "target_endpoint": str(args.target_endpoint),
                "source_object_count": len(refs),
            }
        )
        return result
    except Exception as exc:  # pragma: no cover - depends on external providers
        return {
            "schema_version": "aiat.object-store-copy.v1",
            "mode": "live",
            "status": "blocked",
            "reason": f"live provider unavailable: {type(exc).__name__}: {exc}",
        }
    finally:
        await source.close()
        await target.close()


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = asyncio.run(_run_live(args) if args.live else _run_fixture())
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        if report.get("status") == "blocked":
            print(f"object-store-copy: BLOCKED — {report['reason']}")
            return 2
        counts = report["counts"]
        print(
            "object-store-copy: "
            f"{report['schema_version']} {report.get('mode', 'fixture')} "
            f"PASS={counts['PASS']} FAIL={counts['FAIL']}"
        )
        for case in report["cases"]:
            print(f"  {case['status']}: {case['source_key']} — {case['detail']}")
    if report.get("status") == "blocked":
        return 2
    return 0 if bool(report["passed"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
