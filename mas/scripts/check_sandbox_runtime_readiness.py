"""Check AIAT sandbox declarations and Docker runtime readiness.

Static mode reconciles worker sandbox declarations with the hardened runtime
contract. ``--live`` performs a non-secret Docker Engine inspection and exits
with code 2 when Docker or gVisor is unavailable. The live check proves only
that ``runsc`` is registered; it does not claim a worker canary, network
negative matrix, or Firecracker certification. Pass ``--smoke --image`` with
an immutable image reference for an explicit, bounded gVisor smoke command.
Licence/restriction metadata is outside this operational check.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from mas_core.protocols.worker_manifest import WorkerManifest

MAS_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORKERS_DIR = MAS_ROOT / "workers"
SANDBOX_SCHEMA = "aiat.sandbox-runtime-readiness.v1"
ALLOWED_PROFILES = frozenset({"standard", "restricted", "gvisor", "firecracker"})
ALLOWED_NETWORK_MODES = frozenset({"unrestricted", "egress-allowlist", "egress-deny-all"})
HARDENED_PROFILES = frozenset({"gvisor", "firecracker"})


def _sandbox_value(manifest: WorkerManifest, name: str, default: Any = None) -> Any:
    sandbox = getattr(manifest, "sandbox", None)
    if sandbox is None:
        return default
    value = getattr(sandbox, name, default)
    return default if value is None else value


def inspect_static(*, workers_dir: Path = DEFAULT_WORKERS_DIR) -> dict[str, Any]:
    errors: list[str] = []
    rows: list[dict[str, Any]] = []
    for path in sorted(workers_dir.glob("*.yaml")):
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            manifest = WorkerManifest.model_validate(raw)
        except (OSError, ValueError, yaml.YAMLError, ValidationError) as exc:
            errors.append(f"{path.name}: manifest validation failed")
            rows.append({"worker": path.stem, "status": "fail", "error_type": type(exc).__name__})
            continue
        profile = str(_sandbox_value(manifest, "profile", ""))
        network_mode = str(_sandbox_value(manifest, "network_mode", ""))
        runtime_tier = str(getattr(manifest, "runtime_tier", ""))
        row: dict[str, Any] = {
            "worker": path.stem,
            "runtime_tier": runtime_tier,
            "sandbox_profile": profile,
            "network_mode": network_mode,
            "status": "pass",
        }
        if profile not in ALLOWED_PROFILES:
            row["status"] = "fail"
            errors.append(f"{path.name}: unsupported sandbox profile {profile!r}")
        if network_mode not in ALLOWED_NETWORK_MODES:
            row["status"] = "fail"
            errors.append(f"{path.name}: unsupported sandbox network mode {network_mode!r}")
        if profile in HARDENED_PROFILES and network_mode == "unrestricted":
            row["status"] = "fail"
            errors.append(f"{path.name}: hardened sandbox cannot use unrestricted egress")
        if runtime_tier == "external" and profile not in HARDENED_PROFILES:
            row["status"] = "fail"
            errors.append(f"{path.name}: external worker requires gvisor or firecracker")
        rows.append(row)
    if not rows:
        errors.append("no worker manifests found")
    return {
        "schema_version": SANDBOX_SCHEMA,
        "mode": "static",
        "status": "fail" if errors else "pass",
        "errors": errors,
        "worker_count": len(rows),
        "hardened_worker_count": sum(row.get("sandbox_profile") in HARDENED_PROFILES for row in rows),
        "workers": rows,
        "live_scope": "Docker runtime registration; smoke/canary/network evidence is separate",
    }


def _run(command: list[str], *, timeout: float = 10.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=False, capture_output=True, text=True, timeout=timeout)


def _docker_runtimes() -> tuple[set[str], str | None]:
    if shutil.which("docker") is None:
        return set(), "Docker CLI is not installed"
    try:
        result = _run(["docker", "info", "--format", "{{json .Runtimes}}"])
    except (OSError, subprocess.TimeoutExpired) as exc:
        return set(), f"Docker Engine is unavailable to the Docker CLI ({type(exc).__name__})"
    if result.returncode != 0:
        return set(), "Docker Engine is unavailable to the Docker CLI"
    try:
        value = json.loads(result.stdout.strip())
    except json.JSONDecodeError:
        return set(), "Docker returned invalid runtime metadata"
    if not isinstance(value, dict):
        return set(), "Docker returned an invalid runtime map"
    return {str(name) for name in value}, None


def _run_smoke(image: str) -> tuple[bool, str]:
    if "@sha256:" not in image:
        return False, "smoke image must be pinned by an OCI digest"
    command = [
        "docker",
        "run",
        "--rm",
        "--runtime=runsc",
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--pids-limit=64",
        "--memory=128m",
        "--cpus=0.25",
        image,
        "/bin/true",
    ]
    try:
        result = _run(command, timeout=60.0)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"gVisor smoke command unavailable ({type(exc).__name__})"
    return result.returncode == 0, (
        "gVisor digest-pinned smoke completed"
        if result.returncode == 0
        else "gVisor smoke command failed"
    )


def inspect_live(*, smoke: bool = False, image: str | None = None, require_firecracker: bool = False) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema_version": SANDBOX_SCHEMA,
        "mode": "live",
        "scope": "Docker runtime registration",
        "status": "blocked",
        "errors": [],
        "sandbox_profile": "gvisor",
        "smoke": "not_checked",
        "firecracker": "not_checked",
    }
    runtimes, error = _docker_runtimes()
    report["registered_runtimes"] = sorted(runtimes)
    if error:
        report["reason"] = error
        return report
    if "runsc" not in runtimes:
        report["reason"] = "gVisor runsc runtime is not registered; no runc fallback is permitted"
        return report
    if require_firecracker:
        firecracker = shutil.which("firecracker")
        report["firecracker"] = "available" if firecracker else "blocked_missing_binary"
        if firecracker is None:
            report["reason"] = "Firecracker was required but the binary is unavailable"
            return report
    if smoke:
        if not image:
            report["reason"] = "--smoke requires --image with an immutable digest"
            return report
        passed, reason = _run_smoke(image)
        report["smoke"] = "pass" if passed else "fail"
        if not passed:
            report["status"] = "fail"
            report["reason"] = reason
            report["errors"] = [reason]
            return report
    report["status"] = "pass"
    report["reason"] = (
        "runsc is registered; sandbox smoke/canary/network negative evidence remains separate"
        if not smoke
        else "runsc registration and digest-pinned smoke passed"
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers-dir", type=Path, default=DEFAULT_WORKERS_DIR)
    parser.add_argument("--live", action="store_true", help="inspect the Docker runtime registry")
    parser.add_argument("--smoke", action="store_true", help="run a bounded gVisor smoke command")
    parser.add_argument("--image", help="immutable OCI image for --smoke")
    parser.add_argument("--require-firecracker", action="store_true")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args(argv)
    static = inspect_static(workers_dir=args.workers_dir)
    report: dict[str, Any] = static
    if args.live:
        report = {
            **static,
            "live": inspect_live(
                smoke=args.smoke,
                image=args.image,
                require_firecracker=args.require_firecracker,
            ),
        }
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        print(
            "sandbox runtime readiness: "
            f"status={static['status']} mode={static['mode']} workers={static['worker_count']}"
        )
        if args.live:
            live = report["live"]
            print(f"live: {live['status']} — {live.get('reason', 'unknown reason')}")
    if static["status"] == "fail":
        return 1
    if not args.live:
        return 0
    return 2 if report["live"]["status"] == "blocked" else (1 if report["live"]["status"] == "fail" else 0)


if __name__ == "__main__":
    raise SystemExit(main())
