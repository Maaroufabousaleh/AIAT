"""Run the object-store contract fixture.

Without ``--live`` this validates the adapter contract with a deterministic
in-memory fixture. ``--live`` connects to an explicitly configured
S3-compatible endpoint (MinIO, SeaweedFS, Garage, S3, or another provider) and
runs the same disposable-prefix contract. A missing endpoint/credential or an
unavailable service returns a machine-readable ``blocked`` result with exit
code 2; it never turns unavailable live evidence into a pass.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from mas_core.memory import BlobClient, InMemoryObjectStore, run_object_store_conformance

MAS_ROOT = Path(__file__).resolve().parents[1]
COMPOSE_MINIO_PROBE = MAS_ROOT / "infra" / "compose" / "scripts" / "check-minio-conformance.sh"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit the full report as JSON")
    parser.add_argument("--live", action="store_true", help="run against an S3-compatible endpoint")
    parser.add_argument(
        "--compose-local",
        action="store_true",
        help="run the checked-in MinIO probe inside the private Compose network",
    )
    parser.add_argument(
        "--endpoint",
        default=os.getenv("AIAT_OBJECT_STORE_ENDPOINT") or os.getenv("MINIO_ENDPOINT"),
        help="S3-compatible endpoint (or AIAT_OBJECT_STORE_ENDPOINT/MINIO_ENDPOINT)",
    )
    parser.add_argument(
        "--access-key",
        default=os.getenv("AIAT_OBJECT_STORE_ACCESS_KEY") or os.getenv("MINIO_ACCESS_KEY"),
        help="access key (or AIAT_OBJECT_STORE_ACCESS_KEY/MINIO_ACCESS_KEY)",
    )
    parser.add_argument(
        "--secret-key",
        default=os.getenv("AIAT_OBJECT_STORE_SECRET_KEY") or os.getenv("MINIO_SECRET_KEY"),
        help="secret key (or AIAT_OBJECT_STORE_SECRET_KEY/MINIO_SECRET_KEY)",
    )
    parser.add_argument(
        "--bucket",
        default=os.getenv("AIAT_OBJECT_STORE_BUCKET") or os.getenv("MINIO_BUCKET", "mas-agents"),
        help="disposable test bucket (default: mas-agents)",
    )
    parser.add_argument(
        "--region",
        default=os.getenv("AIAT_OBJECT_STORE_REGION", "us-east-1"),
        help="S3 region (default: us-east-1)",
    )
    parser.add_argument(
        "--project-id",
        default=os.getenv("AIAT_OBJECT_STORE_CONFORMANCE_PROJECT", "aiat-conformance-live"),
        help="disposable project prefix (default: aiat-conformance-live)",
    )
    return parser


def _run_compose_local(args: argparse.Namespace) -> dict[str, Any]:
    """Run the private-network MinIO probe without copying credentials to host.

    The development Compose file intentionally does not publish MinIO's S3
    port. A host-side ``BlobClient`` therefore cannot resolve ``minio:9000``;
    the checked-in probe executes the same contract inside the orchestrator
    container, where the existing endpoint and credential boundary apply.
    """
    base = {
        "schema_version": "aiat.object-store-conformance.v1",
        "mode": "local-live",
        "adapter_type": "s3-compatible",
        "provider": "minio",
        "transport": "docker-exec-private-network",
    }
    if shutil.which("docker") is None:
        return {**base, "status": "blocked", "reason": "Docker CLI is unavailable for the private-network MinIO probe"}
    if not COMPOSE_MINIO_PROBE.is_file():
        return {**base, "status": "blocked", "reason": "checked-in private-network MinIO probe is missing"}
    project_id = str(args.project_id).strip()
    if not project_id:
        return {**base, "status": "blocked", "reason": "project ID must not be empty"}
    try:
        result = subprocess.run(
            ["bash", str(COMPOSE_MINIO_PROBE), project_id],
            cwd=MAS_ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=45.0,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            **base,
            "status": "blocked",
            "reason": f"private-network MinIO probe unavailable: {type(exc).__name__}",
        }
    try:
        payload = json.loads(result.stdout.strip())
    except json.JSONDecodeError:
        return {
            **base,
            "status": "blocked",
            "reason": "private-network MinIO probe returned invalid JSON",
        }
    if not isinstance(payload, dict):
        return {
            **base,
            "status": "blocked",
            "reason": "private-network MinIO probe returned an invalid report",
        }
    report = {**payload, **base, "mode": "local-live"}
    if report.get("status") not in {"pass", "blocked", "fail"}:
        report["status"] = "pass" if report.get("passed") is True else "fail"
    if result.returncode != 0 and report.get("status") not in {"blocked", "fail"}:
        report["status"] = "blocked"
        report["reason"] = "private-network MinIO probe did not complete successfully"
    return report


async def _run_live(args: argparse.Namespace) -> dict[str, Any]:
    missing = [
        name
        for name, value in (
            ("endpoint", args.endpoint),
            ("access_key", args.access_key),
            ("secret_key", args.secret_key),
        )
        if not value
    ]
    if missing:
        return {
            "schema_version": "aiat.object-store-conformance.v1",
            "mode": "live",
            "adapter_type": "s3-compatible",
            "status": "blocked",
            "reason": f"missing live configuration: {', '.join(missing)}",
        }

    client = BlobClient(
        str(args.endpoint),
        access_key=str(args.access_key),
        secret_key=str(args.secret_key),
        bucket=str(args.bucket),
        region=str(args.region),
    )
    try:
        await client.connect()
        report = await run_object_store_conformance(
            client,
            project_id=str(args.project_id),
            bucket=str(args.bucket),
        )
        result = report.as_dict()
        result.update({"mode": "live", "endpoint": str(args.endpoint)})
        return result
    except Exception as exc:  # pragma: no cover - depends on external provider
        return {
            "schema_version": "aiat.object-store-conformance.v1",
            "mode": "live",
            "adapter_type": "s3-compatible",
            "status": "blocked",
            "endpoint": str(args.endpoint),
            "reason": f"live provider unavailable: {type(exc).__name__}: {exc}",
        }
    finally:
        await client.close()


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    if args.compose_local and not args.live:
        return {
            "schema_version": "aiat.object-store-conformance.v1",
            "mode": "local-live",
            "adapter_type": "s3-compatible",
            "status": "blocked",
            "reason": "--compose-local requires --live",
        }
    if args.compose_local:
        return _run_compose_local(args)
    if args.live:
        return await _run_live(args)
    report = await run_object_store_conformance(InMemoryObjectStore())
    result = report.as_dict()
    result["mode"] = "fixture"
    return result


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = asyncio.run(_run(args))
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        if report.get("status") == "blocked":
            print(f"object-store-conformance: BLOCKED — {report['reason']}")
            return 2
        counts = report["counts"]
        print(
            "object-store-conformance: "
            f"{report['schema_version']} {report.get('mode', 'fixture')} {report['adapter_type']} "
            f"PASS={counts['PASS']} FAIL={counts['FAIL']}"
        )
        for case in report["cases"]:
            print(f"  {case['status']}: {case['case_id']} — {case['detail']}")
    if report.get("status") == "blocked":
        return 2
    return 0 if bool(report["passed"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
