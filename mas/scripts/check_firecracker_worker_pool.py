"""Check the explicit Firecracker high-risk worker pool contract.

Static mode validates the AIAT launch specification without invoking a
launcher. ``--live`` checks only that the configured launcher and Firecracker
binary are discoverable on the host; it remains blocked without both and never
falls back to Docker/runc. A live readiness pass still does not certify a
microVM canary, network-negative matrix, provider call, or recovery exercise.
Licence/restriction metadata is outside this operational check.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

MAS_ROOT = Path(__file__).resolve().parents[1]
CORE_ROOT = MAS_ROOT / "packages" / "mas-core"
if CORE_ROOT.exists() and str(CORE_ROOT) not in sys.path:
    sys.path.insert(0, str(CORE_ROOT))

from mas_core.worker_registry.firecracker import FirecrackerLaunchSpec  # noqa: E402

CHECK_SCHEMA = "aiat.firecracker-worker-pool-readiness.v1"
KERNEL_SHA = "a" * 64
ROOTFS_SHA = "b" * 64


def default_spec(*, launcher: str = "aiat-firecracker-launcher") -> FirecrackerLaunchSpec:
    return FirecrackerLaunchSpec(
        launcher=launcher,
        kernel_path="/var/lib/aiat/firecracker/kernel-v1",
        kernel_sha256=KERNEL_SHA,
        rootfs_path="/var/lib/aiat/firecracker/rootfs-v1.ext4",
        rootfs_sha256=ROOTFS_SHA,
        artifact_dir="/var/lib/aiat/firecracker/artifacts",
        network_mode="egress-deny-all",
        secret_refs=("gateway/worker-token",),
    )


def inspect_static(*, launcher: str = "aiat-firecracker-launcher") -> dict[str, Any]:
    try:
        spec = default_spec(launcher=launcher)
    except ValueError as exc:
        return {
            "schema_version": CHECK_SCHEMA,
            "mode": "static",
            "status": "fail",
            "reason": "firecracker_launch_spec_invalid",
            "error_type": type(exc).__name__,
            "mutation_performed": False,
            "licence_metadata_is_gate": False,
        }
    return {
        "schema_version": CHECK_SCHEMA,
        "mode": "static",
        "status": "pass",
        "launch_spec": spec.public_projection(),
        "argv_length": len(spec.argv()),
        "mutation_performed": False,
        "sandbox_execution_performed": False,
        "network_access_performed": False,
        "licence_metadata_is_gate": False,
    }


def inspect_live(*, launcher: str = "aiat-firecracker-launcher") -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema_version": CHECK_SCHEMA,
        "mode": "live",
        "status": "blocked",
        "launcher": launcher,
        "launcher_available": False,
        "firecracker_available": False,
        "microvm_smoke": "not_checked",
        "mutation_performed": False,
        "sandbox_execution_performed": False,
        "network_access_performed": False,
        "licence_metadata_is_gate": False,
    }
    launcher_path = shutil.which(launcher)
    firecracker_path = shutil.which("firecracker")
    report["launcher_available"] = launcher_path is not None
    report["firecracker_available"] = firecracker_path is not None
    if launcher_path is None:
        report["reason"] = "certified Firecracker launcher is unavailable"
        return report
    if firecracker_path is None:
        report["reason"] = "Firecracker binary is unavailable"
        return report
    report["status"] = "pass"
    report["reason"] = (
        "launcher and Firecracker binary are discoverable; microVM smoke, "
        "network, provider, and recovery evidence remains separate"
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--launcher", default="aiat-firecracker-launcher")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    static = inspect_static(launcher=args.launcher)
    report: dict[str, Any] = static
    if args.live:
        report = {**static, "live": inspect_live(launcher=args.launcher)}
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        print(f"Firecracker worker pool readiness: {report['status']}")
        if args.live:
            print(f"live: {report['live']['status']} — {report['live'].get('reason', 'unknown reason')}")
    if static["status"] == "fail":
        return 1
    if not args.live:
        return 0
    return 2 if report["live"]["status"] == "blocked" else (1 if report["live"]["status"] == "fail" else 0)


if __name__ == "__main__":
    raise SystemExit(main())
