"""Run a bounded MinIO/SeaweedFS object-store comparison.

The default mode uses the deterministic in-memory adapter and verifies the
benchmark contract without network or storage mutation outside its disposable
fixture. ``--live`` requires explicit credentials for both named providers,
uses short-lived project-scoped keys, and returns exit code 2 when either
provider is unavailable. The report never chooses a primary provider; an
operator must review performance, reliability, provenance, migration, and
restore evidence before any cutover.
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
)
from mas_core.memory.object_store_benchmark import (
    DEFAULT_PAYLOAD_SIZES,
    ObjectStoreBenchmarkConfig,
    run_object_store_benchmark,
)

SCHEMA = "aiat.object-store-benchmark.v1"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit a machine-readable report")
    parser.add_argument(
        "--live",
        action="store_true",
        help="benchmark both configured S3-compatible providers",
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
        default=os.getenv("AIAT_OBJECT_STORE_BENCHMARK_PROJECT", "aiat-benchmark"),
    )
    parser.add_argument(
        "--payload-size",
        action="append",
        type=int,
        dest="payload_sizes",
        help="bounded payload size in bytes; repeat to override the defaults",
    )
    return parser


def _config(args: argparse.Namespace) -> ObjectStoreBenchmarkConfig:
    sizes = tuple(args.payload_sizes or DEFAULT_PAYLOAD_SIZES)
    return ObjectStoreBenchmarkConfig(
        payload_sizes=sizes,
        project_id=str(args.project_id),
        bucket=str(args.bucket),
    )


async def _run_live_provider(
    *,
    name: str,
    endpoint: str,
    access_key: str,
    secret_key: str,
    config: ObjectStoreBenchmarkConfig,
) -> dict[str, Any]:
    client = BlobClient(
        endpoint,
        access_key=access_key,
        secret_key=secret_key,
        bucket=config.bucket,
    )
    try:
        await client.connect()
        report = await run_object_store_benchmark(client, provider=name, config=config)
        return report.as_dict()
    except Exception as exc:  # pragma: no cover - external provider boundary
        return {
            "schema_version": SCHEMA,
            "provider": name,
            "status": "blocked",
            "error_type": type(exc).__name__,
            "scope": "disposable benchmark; no provider selection decision",
        }
    finally:
        await client.close()


def _blocked_live_report(missing: list[str]) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA,
        "mode": "live",
        "status": "blocked",
        "reason": f"missing live configuration: {', '.join(missing)}",
        "providers": [],
        "decision": "operator_review_required",
        "scope": "disposable benchmark; no provider selection decision",
    }


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    config = _config(args)
    if not args.live:
        report = await run_object_store_benchmark(
            InMemoryObjectStore(bucket=config.bucket),
            provider="in-memory-fixture",
            config=config,
        )
        return {"mode": "fixture", "decision": "not_applicable", **report.as_dict()}

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
        return _blocked_live_report(missing)

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
    if "blocked" in statuses:
        status = "blocked"
    elif "fail" in statuses:
        status = "fail"
    else:
        status = "pass"
    comparison = [
        {
            "provider": provider.get("provider"),
            "status": provider.get("status"),
            "total_roundtrip_ms": round(
                sum(float(row.get("roundtrip_ms", 0.0)) for row in provider.get("rows", [])),
                3,
            ),
        }
        for provider in providers
    ]
    return {
        "schema_version": SCHEMA,
        "mode": "live",
        "status": status,
        "providers": providers,
        "comparison": comparison,
        "decision": "operator_review_required",
        "scope": (
            "disposable upload/download checksum read-back/delete benchmark; "
            "no routing or cutover decision"
        ),
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = asyncio.run(_run(args))
    except (TypeError, ValueError) as exc:
        report = {
            "schema_version": SCHEMA,
            "mode": "fixture" if not args.live else "live",
            "status": "fail",
            "reason": str(exc),
        }
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        print(f"object-store-benchmarks: {report.get('status', 'fail').upper()}")
        if report.get("reason"):
            print(f"  {report['reason']}")
    status = report.get("status")
    if status == "blocked":
        return 2
    return 0 if status == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
