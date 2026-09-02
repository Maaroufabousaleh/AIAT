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
DEFAULT_COMPOSE = MAS_ROOT / "infra" / "compose" / "docker-compose.yml"
SANDBOX_SCHEMA = "aiat.sandbox-runtime-readiness.v1"
ALLOWED_PROFILES = frozenset({"standard", "restricted", "gvisor", "firecracker"})
ALLOWED_NETWORK_MODES = frozenset({"unrestricted", "egress-allowlist", "egress-deny-all"})
HARDENED_PROFILES = frozenset({"gvisor", "firecracker"})
OPENCODE_SERVICE = "opencode-runtime"
OPENCODE_NETWORK = "internal"
OPENCODE_MAX_MEMORY_BYTES = 1024 * 1024 * 1024
OPENCODE_MAX_CPUS = 1.0
OPENCODE_MAX_PIDS = 256


def _sandbox_value(manifest: WorkerManifest, name: str, default: Any = None) -> Any:
    sandbox = getattr(manifest, "sandbox", None)
    if sandbox is None:
        return default
    value = getattr(sandbox, name, default)
    return default if value is None else value


def _service_networks(service: dict[str, Any]) -> set[str]:
    networks = service.get("networks") or []
    if isinstance(networks, dict):
        return {str(name) for name in networks}
    if isinstance(networks, list):
        return {str(name) for name in networks}
    return set()


def _memory_bytes(value: Any) -> int | None:
    if isinstance(value, int) and value > 0:
        return value
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    suffixes = (("g", 1024**3), ("m", 1024**2), ("k", 1024))
    for suffix, multiplier in suffixes:
        if text.endswith(suffix):
            try:
                return int(float(text[:-1]) * multiplier)
            except ValueError:
                return None
    try:
        return int(text)
    except ValueError:
        return None


def _inspect_opencode_runtime(compose_path: Path) -> dict[str, Any]:
    """Validate the AIAT-owned Compose boundary around the untrusted runtime."""
    errors: list[str] = []
    try:
        compose = yaml.safe_load(compose_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        return {"status": "fail", "errors": [f"Compose could not be loaded: {type(exc).__name__}"]}
    services = compose.get("services") or {}
    service = services.get(OPENCODE_SERVICE) if isinstance(services, dict) else None
    if not isinstance(service, dict):
        return {"status": "fail", "errors": [f"Compose service {OPENCODE_SERVICE!r} is missing"]}

    if _service_networks(service) != {OPENCODE_NETWORK}:
        errors.append(f"{OPENCODE_SERVICE}: networks must be exactly [{OPENCODE_NETWORK!r}]")
    if service.get("ports"):
        errors.append(f"{OPENCODE_SERVICE}: host ports must not be published")
    if service.get("read_only") is not True:
        errors.append(f"{OPENCODE_SERVICE}: read_only must be true")
    user = str(service.get("user") or "").strip().lower()
    if not user or user in {"0", "0:0", "root", "root:root"}:
        errors.append(f"{OPENCODE_SERVICE}: runtime user must be non-root")
    cap_drop = {str(value).upper() for value in (service.get("cap_drop") or [])}
    if "ALL" not in cap_drop:
        errors.append(f"{OPENCODE_SERVICE}: cap_drop must include ALL")
    security_opts = {str(value).lower().replace("=", ":") for value in (service.get("security_opt") or [])}
    if "no-new-privileges:true" not in security_opts:
        errors.append(f"{OPENCODE_SERVICE}: no-new-privileges must be enabled")
    pids_limit = service.get("pids_limit")
    if not isinstance(pids_limit, int) or not 1 <= pids_limit <= OPENCODE_MAX_PIDS:
        errors.append(f"{OPENCODE_SERVICE}: pids_limit must be an integer <= {OPENCODE_MAX_PIDS}")
    memory = _memory_bytes(service.get("mem_limit"))
    if memory is None or memory > OPENCODE_MAX_MEMORY_BYTES:
        errors.append(f"{OPENCODE_SERVICE}: mem_limit must be <= 1g")
    try:
        cpus = float(service.get("cpus"))
    except (TypeError, ValueError):
        cpus = 0.0
    if cpus <= 0 or cpus > OPENCODE_MAX_CPUS:
        errors.append(f"{OPENCODE_SERVICE}: cpus must be > 0 and <= {OPENCODE_MAX_CPUS}")

    tmpfs_entries = service.get("tmpfs") or []
    tmpfs_by_path: dict[str, str] = {}
    for entry in tmpfs_entries:
        if isinstance(entry, str):
            path, _, options = entry.partition(":")
            tmpfs_by_path[path] = options.lower()
    for required_path in ("/tmp", "/runtime"):
        options = tmpfs_by_path.get(required_path)
        if options is None:
            errors.append(f"{OPENCODE_SERVICE}: required tmpfs {required_path} is missing")
            continue
        if "noexec" not in options or "nosuid" not in options:
            errors.append(f"{OPENCODE_SERVICE}: tmpfs {required_path} must use noexec and nosuid")
    for volume in service.get("volumes") or []:
        if isinstance(volume, str) and "/var/run/docker.sock" in volume:
            errors.append(f"{OPENCODE_SERVICE}: Docker socket mount is forbidden")

    return {
        "status": "fail" if errors else "pass",
        "errors": errors,
        "service": OPENCODE_SERVICE,
        "network": sorted(_service_networks(service)),
        "user": user,
        "read_only": service.get("read_only") is True,
        "cap_drop_all": "ALL" in cap_drop,
        "no_new_privileges": "no-new-privileges:true" in security_opts,
        "pids_limit": pids_limit,
        "memory_bytes": memory,
        "cpus": cpus,
        "tmpfs_paths": sorted(tmpfs_by_path),
        "scope": "Compose boundary only; gVisor/Firecracker smoke, canary, and network evidence remain separate",
    }


def inspect_static(
    *,
    workers_dir: Path = DEFAULT_WORKERS_DIR,
    compose_path: Path = DEFAULT_COMPOSE,
) -> dict[str, Any]:
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
    opencode_runtime = _inspect_opencode_runtime(compose_path)
    errors.extend(str(error) for error in opencode_runtime.get("errors", []))
    return {
        "schema_version": SANDBOX_SCHEMA,
        "mode": "static",
        "status": "fail" if errors else "pass",
        "errors": errors,
        "worker_count": len(rows),
        "hardened_worker_count": sum(row.get("sandbox_profile") in HARDENED_PROFILES for row in rows),
        "workers": rows,
        "compose": str(compose_path),
        "opencode_runtime": opencode_runtime,
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
    parser.add_argument("--compose", type=Path, default=DEFAULT_COMPOSE)
    parser.add_argument("--live", action="store_true", help="inspect the Docker runtime registry")
    parser.add_argument("--smoke", action="store_true", help="run a bounded gVisor smoke command")
    parser.add_argument("--image", help="immutable OCI image for --smoke")
    parser.add_argument("--require-firecracker", action="store_true")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args(argv)
    static = inspect_static(workers_dir=args.workers_dir, compose_path=args.compose)
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
