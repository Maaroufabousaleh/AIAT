"""Run a bounded, scalar-only object-store resource profile.

The fixture mode profiles the deterministic in-memory adapter. ``--live``
requires explicit MinIO and SeaweedFS credentials and profiles the existing
checksum benchmark against each endpoint. The report contains only timing,
process-memory, checksum, and cleanup metadata; it never selects a provider or
prints credentials. Missing resource measurement or provider configuration is
blocked rather than treated as a pass.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from typing import Any

from mas_core.memory import BlobClient, InMemoryObjectStore
from mas_core.memory.object_store_resource_profile import (
    DEFAULT_RESOURCE_PROFILE_CONCURRENCY,
    DEFAULT_RESOURCE_PROFILE_PAYLOAD_SIZES,
    OBJECT_STORE_RESOURCE_PROFILE_SCHEMA,
    ObjectStoreResourceProfileConfig,
    run_object_store_resource_profile,
)

SCHEMA = OBJECT_STORE_RESOURCE_PROFILE_SCHEMA


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit a machine-readable report")
    parser.add_argument(
        "--live",
        action="store_true",
        help="profile both configured S3-compatible providers",
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
        default=os.getenv("AIAT_OBJECT_STORE_RESOURCE_PROFILE_PROJECT", "aiat-object-store-resource-profile"),
    )
    parser.add_argument(
        "--payload-size",
        action="append",
        type=int,
        dest="payload_sizes",
        help="bounded payload size in bytes; repeat to override the defaults",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=int(
            os.getenv(
                "AIAT_OBJECT_STORE_RESOURCE_PROFILE_CONCURRENCY",
                str(DEFAULT_RESOURCE_PROFILE_CONCURRENCY),
            )
        ),
        help="bounded simultaneous cases per payload size (default: 4)",
    )
    return parser


def _config(args: argparse.Namespace) -> ObjectStoreResourceProfileConfig:
    sizes = tuple(args.payload_sizes or DEFAULT_RESOURCE_PROFILE_PAYLOAD_SIZES)
    return ObjectStoreResourceProfileConfig(
        payload_sizes=sizes,
        project_id=str(args.project_id),
        bucket=str(args.bucket),
        concurrency=int(args.concurrency),
    )


def _plan(config: ObjectStoreResourceProfileConfig) -> dict[str, Any]:
    return {
        "payload_sizes_bytes": list(config.payload_sizes),
        "concurrency": config.concurrency,
        "case_count": len(config.payload_sizes) * config.concurrency,
        "total_payload_bytes": sum(config.payload_sizes) * config.concurrency,
    }


async def _run_live_provider(
    *,
    name: str,
    endpoint: str,
    access_key: str,
    secret_key: str,
    config: ObjectStoreResourceProfileConfig,
) -> dict[str, Any]:
    client = BlobClient(
        endpoint,
        access_key=access_key,
        secret_key=secret_key,
        bucket=config.bucket,
    )
    try:
        await client.connect()
        report = await run_object_store_resource_profile(
            client,
            provider=name,
            config=config,
        )
        return {"benchmark_plan": _plan(config), **report.as_dict()}
    except Exception as exc:  # pragma: no cover - external provider boundary
        return {
            "schema_version": SCHEMA,
            "provider": name,
            "status": "blocked",
            "error_type": type(exc).__name__,
            "scope": "disposable resource profile; no provider selection decision",
        }
    finally:
        await client.close()


def _blocked_live_report(
    missing: list[str],
    *,
    config: ObjectStoreResourceProfileConfig,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA,
        "mode": "live",
        "status": "blocked",
        "reason": f"missing live configuration: {', '.join(missing)}",
        "providers": [],
        "benchmark_plan": _plan(config),
        "decision": "operator_review_required",
        "scope": "disposable resource profile; no provider selection decision",
    }


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    config = _config(args)
    if not args.live:
        report = await run_object_store_resource_profile(
            InMemoryObjectStore(bucket=config.bucket),
            provider="in-memory-fixture",
            config=config,
        )
        return {
            "mode": "fixture",
            "decision": "not_applicable",
            "benchmark_plan": _plan(config),
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
            "measurement_source": provider.get("measurement_source"),
            "wall_time_ms": provider.get("wall_time_ms"),
            "cpu_time_ms": provider.get("cpu_time_ms"),
            "rss_peak_bytes": provider.get("rss_peak_bytes"),
        }
        for provider in providers
    ]
    return {
        "schema_version": SCHEMA,
        "mode": "live",
        "status": status,
        "benchmark_plan": _plan(config),
        "providers": providers,
        "comparison": comparison,
        "decision": "operator_review_required",
        "scope": (
            "disposable checksum benchmark resource profile; scalar timing and "
            "process-memory evidence only; no routing or provider decision"
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
        print(f"object-store-resource-profile: {report.get('status', 'fail').upper()}")
        if report.get("reason"):
            print(f"  {report['reason']}")
    status = report.get("status")
    if status == "blocked":
        return 2
    return 0 if status == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
