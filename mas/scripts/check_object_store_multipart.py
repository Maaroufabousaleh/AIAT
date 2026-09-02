"""Run a bounded MinIO/SeaweedFS multipart upload comparison.

Fixture mode is deterministic and has no network side effects. ``--live``
requires explicit credentials for both named S3-compatible providers and uses
only a reserved project prefix. The checker verifies provider-managed
create/part/complete/abort operations, checksum read-back, and cleanup; it
never chooses a provider or treats licence metadata as a gate.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from typing import Any

from mas_core.memory import (
    BlobClient,
    InMemoryObjectStore,
    MultipartUploadConfig,
    run_object_store_multipart_probe,
)

SCHEMA = "aiat.object-store-multipart.v1"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit a machine-readable report")
    parser.add_argument(
        "--live",
        action="store_true",
        help="probe both configured S3-compatible providers",
    )
    parser.add_argument("--minio-endpoint", default=os.getenv("AIAT_MINIO_ENDPOINT"))
    parser.add_argument("--minio-access-key", default=os.getenv("AIAT_MINIO_ACCESS_KEY"))
    parser.add_argument("--minio-secret-key", default=os.getenv("AIAT_MINIO_SECRET_KEY"))
    parser.add_argument("--seaweedfs-endpoint", default=os.getenv("AIAT_SEAWEEDFS_ENDPOINT"))
    parser.add_argument("--seaweedfs-access-key", default=os.getenv("AIAT_SEAWEEDFS_ACCESS_KEY"))
    parser.add_argument("--seaweedfs-secret-key", default=os.getenv("AIAT_SEAWEEDFS_SECRET_KEY"))
    parser.add_argument("--bucket", default=os.getenv("AIAT_OBJECT_STORE_BUCKET", "mas-agents"))
    parser.add_argument(
        "--project-id",
        default=os.getenv("AIAT_OBJECT_STORE_MULTIPART_PROJECT", "aiat-multipart-benchmark"),
    )
    parser.add_argument(
        "--payload-size",
        action="append",
        type=int,
        dest="payload_sizes",
        help="bounded multipart payload size in bytes; repeat to override defaults",
    )
    parser.add_argument(
        "--part-size",
        type=int,
        default=int(os.getenv("AIAT_OBJECT_STORE_MULTIPART_PART_SIZE", str(5 * 1024 * 1024))),
        help="multipart part size in bytes (minimum 5 MiB)",
    )
    return parser


def _config(args: argparse.Namespace) -> MultipartUploadConfig:
    return MultipartUploadConfig(
        payload_sizes=tuple(args.payload_sizes) if args.payload_sizes else MultipartUploadConfig().payload_sizes,
        part_size_bytes=int(args.part_size),
        project_id=str(args.project_id),
        bucket=str(args.bucket),
    )


def _plan(config: MultipartUploadConfig) -> dict[str, Any]:
    return {
        "payload_sizes_bytes": list(config.payload_sizes),
        "part_size_bytes": config.part_size_bytes,
        "expected_part_counts": [
            (size + config.part_size_bytes - 1) // config.part_size_bytes
            for size in config.payload_sizes
        ],
        "total_payload_bytes": sum(config.payload_sizes),
    }


def _blocked_live_report(missing: list[str], *, config: MultipartUploadConfig) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA,
        "mode": "live",
        "status": "blocked",
        "reason": f"missing live configuration: {', '.join(missing)}",
        "providers": [],
        "multipart_plan": _plan(config),
        "decision": "operator_review_required",
        "scope": "disposable multipart benchmark; no provider selection decision",
    }


async def _run_live_provider(
    *,
    name: str,
    endpoint: str,
    access_key: str,
    secret_key: str,
    config: MultipartUploadConfig,
) -> dict[str, Any]:
    client = BlobClient(
        endpoint,
        access_key=access_key,
        secret_key=secret_key,
        bucket=config.bucket,
    )
    try:
        await client.connect()
        report = await run_object_store_multipart_probe(
            client,
            provider=name,
            config=config,
        )
        return {"multipart_plan": _plan(config), **report.as_dict()}
    except Exception as exc:  # pragma: no cover - external provider boundary
        return {
            "schema_version": SCHEMA,
            "provider": name,
            "status": "blocked",
            "error_type": type(exc).__name__,
            "scope": "disposable multipart benchmark; no provider selection decision",
        }
    finally:
        await client.close()


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    config = _config(args)
    if not args.live:
        report = await run_object_store_multipart_probe(
            InMemoryObjectStore(bucket=config.bucket),
            provider="in-memory-fixture",
            config=config,
        )
        return {
            "schema_version": SCHEMA,
            "mode": "fixture",
            "decision": "not_applicable",
            "multipart_plan": _plan(config),
            **report.as_dict(),
        }

    provider_inputs = (
        ("minio", args.minio_endpoint, args.minio_access_key, args.minio_secret_key),
        (
            "seaweedfs",
            args.seaweedfs_endpoint,
            args.seaweedfs_access_key,
            args.seaweedfs_secret_key,
        ),
    )
    missing = [
        f"{name}.{field}"
        for name, endpoint, access_key, secret_key in provider_inputs
        for field, value in (
            ("endpoint", endpoint),
            ("access_key", access_key),
            ("secret_key", secret_key),
        )
        if not value
    ]
    if missing:
        return _blocked_live_report(missing, config=config)

    providers = [
        await _run_live_provider(
            name=name,
            endpoint=str(endpoint),
            access_key=str(access_key),
            secret_key=str(secret_key),
            config=config,
        )
        for name, endpoint, access_key, secret_key in provider_inputs
    ]
    statuses = {str(provider.get("status")) for provider in providers}
    status = "blocked" if "blocked" in statuses else "fail" if "fail" in statuses else "pass"
    return {
        "schema_version": SCHEMA,
        "mode": "live",
        "status": status,
        "multipart_plan": _plan(config),
        "providers": providers,
        "decision": "operator_review_required",
        "scope": "disposable multipart benchmark; no provider selection decision",
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = asyncio.run(_run(args))
    except (TypeError, ValueError) as exc:
        report = {
            "schema_version": SCHEMA,
            "mode": "live" if args.live else "fixture",
            "status": "fail",
            "reason": str(exc),
        }
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        print(f"object-store-multipart: {report.get('status', 'fail').upper()}")
        if report.get("reason"):
            print(f"  {report['reason']}")
    if report.get("status") == "blocked":
        return 2
    return 0 if report.get("status") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
