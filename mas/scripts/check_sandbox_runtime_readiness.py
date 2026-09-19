"""Check AIAT sandbox declarations and Docker runtime readiness.

Static mode reconciles worker sandbox declarations with the hardened runtime
contract. ``--live`` performs a non-secret Docker Engine inspection and exits
with code 2 when Docker or the required development runtime is unavailable.
The development profile requires gVisor/runsc for ``sandboxed`` workers and
uses Kata runtime-rs/QEMU for ``vm_isolated`` workers when the host is capable.
Kata is never a silent fallback and the selected Kata VMM is host metadata,
not a worker policy value.
The live check does not claim a worker canary, network negative matrix, or
native-Linux release certification. Pass ``--smoke --image`` with an
immutable image reference for an explicit, bounded smoke command.
Licence/restriction metadata is outside this operational check.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import platform
import re
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from mas_core.protocols.worker_manifest import WorkerManifest
from mas_core.sandbox_policy import (
    CANONICAL_SANDBOX_CLASSES,
    HARDENED_SANDBOX_CLASSES,
    SUPPORTED_SANDBOX_PROFILES,
    canonical_sandbox_class,
)

MAS_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORKERS_DIR = MAS_ROOT / "workers"
DEFAULT_COMPOSE = MAS_ROOT / "infra" / "compose" / "docker-compose.yml"
SANDBOX_SCHEMA = "aiat.sandbox-runtime-readiness.v1"
ALLOWED_PROFILES = SUPPORTED_SANDBOX_PROFILES
CANONICAL_PROFILES = CANONICAL_SANDBOX_CLASSES
ALLOWED_NETWORK_MODES = frozenset({"unrestricted", "egress-allowlist", "egress-deny-all"})
HARDENED_PROFILES = HARDENED_SANDBOX_CLASSES
OPENCODE_SERVICE = "opencode-runtime"
OPENCODE_NETWORK = "internal"
OPENCODE_MAX_MEMORY_BYTES = 1024 * 1024 * 1024
OPENCODE_MAX_CPUS = 1.0
OPENCODE_MAX_PIDS = 256
KATA_RUNTIME_NAME = os.environ.get("AIAT_KATA_RUNTIME_NAME", "kata")
KATA_INSTALL_ROOT = Path(os.environ.get("AIAT_KATA_INSTALL_ROOT", "/opt/kata"))
KATA_RUNTIME_SHIM = Path(
    os.environ.get(
        "AIAT_KATA_RUNTIME_SHIM",
        "/opt/kata/runtime-rs/bin/containerd-shim-kata-v2",
    )
)
KATA_RUNTIME_CONFIG = Path(
    os.environ.get(
        "AIAT_KATA_RUNTIME_CONFIG",
        "/opt/kata/share/defaults/kata-containers/runtime-rs/configuration-qemu-runtime-rs.toml",
    )
)
KATA_QEMU_BINARY = Path(os.environ.get("AIAT_KATA_QEMU_BINARY", "/opt/kata/bin/qemu-system-x86_64"))
KATA_VIRTIOFS_BINARY = KATA_INSTALL_ROOT / "libexec" / "virtiofsd"
KATA_GUEST_IMAGE = Path(
    os.environ.get("AIAT_KATA_GUEST_IMAGE", "/opt/kata/share/kata-containers/kata-containers.img")
)
KATA_VERSION_FILE = KATA_INSTALL_ROOT / "VERSION"
DIGEST_RE = re.compile(r"@sha256:[0-9a-fA-F]{64}$")


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
        "scope": "Compose boundary only; gVisor/Kata smoke, canary, and network evidence remain separate",
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
        try:
            sandbox_class = canonical_sandbox_class(profile)
        except ValueError:
            sandbox_class = "invalid"
        network_mode = str(_sandbox_value(manifest, "network_mode", ""))
        runtime_tier = str(getattr(manifest, "runtime_tier", ""))
        row: dict[str, Any] = {
            "worker": path.stem,
            "runtime_tier": runtime_tier,
            "sandbox_profile": profile,
            "sandbox_class": sandbox_class,
            "network_mode": network_mode,
            "status": "pass",
        }
        if profile not in ALLOWED_PROFILES:
            row["status"] = "fail"
            errors.append(f"{path.name}: unsupported sandbox profile {profile!r}")
        if network_mode not in ALLOWED_NETWORK_MODES:
            row["status"] = "fail"
            errors.append(f"{path.name}: unsupported sandbox network mode {network_mode!r}")
        if sandbox_class in HARDENED_PROFILES and network_mode == "unrestricted":
            row["status"] = "fail"
            errors.append(f"{path.name}: hardened sandbox cannot use unrestricted egress")
        if runtime_tier == "external" and sandbox_class not in HARDENED_PROFILES:
            row["status"] = "fail"
            errors.append(f"{path.name}: external worker requires sandboxed (gVisor) or vm_isolated (Kata)")
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
        "hardened_worker_count": sum(row.get("sandbox_class") in HARDENED_PROFILES for row in rows),
        "workers": rows,
        "compose": str(compose_path),
        "opencode_runtime": opencode_runtime,
        "live_scope": "Docker runtime registration; gVisor smoke, optional Kata probe, canary, and network evidence are separate",
    }


def _run(command: list[str], *, timeout: float = 10.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=False, capture_output=True, text=True, timeout=timeout)


def _docker_command(*args: str) -> list[str]:
    """Build a Docker CLI command without depending on the ambient context."""

    context = str(os.environ.get("AIAT_DOCKER_CONTEXT") or os.environ.get("DOCKER_CONTEXT") or "").strip()
    command = ["docker"]
    if context:
        command.extend(("--context", context))
    command.extend(args)
    return command


def _docker_runtimes() -> tuple[set[str], str | None]:
    if shutil.which("docker") is None:
        return set(), "Docker CLI is not installed"
    try:
        result = _run(_docker_command("info", "--format", "{{json .Runtimes}}"))
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


def _validate_digest_image(image: str | None) -> tuple[bool, str]:
    if not image or not DIGEST_RE.search(image.strip()):
        return False, "smoke image must be pinned by an OCI digest"
    return True, ""


def _run_smoke(image: str, *, runtime: str) -> tuple[bool, str]:
    valid, reason = _validate_digest_image(image)
    if not valid:
        return False, reason
    runtime_name = str(runtime or "").strip().lower()
    if not runtime_name:
        return False, "smoke runtime is required"
    runtime_label = "Kata" if runtime_name == "kata" or runtime_name.startswith("kata-") else "gVisor"
    command = [
        *_docker_command(),
        "run",
        "--rm",
        f"--runtime={runtime_name}",
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
        return False, f"{runtime_label} smoke command unavailable ({type(exc).__name__})"
    return result.returncode == 0, (
        f"{runtime_label} ({runtime_name}) digest-pinned smoke completed"
        if result.returncode == 0
        else f"{runtime_label} ({runtime_name}) smoke command failed"
    )


def _kata_host_prerequisites() -> dict[str, Any]:
    """Inspect the host-side Kata runtime-rs/QEMU installation.

    Package/archive presence is deliberately insufficient.  A Kata host must
    have the selected runtime inputs and a usable KVM device before a guest
    smoke can be considered.
    """

    paths = {
        "runtime_shim": KATA_RUNTIME_SHIM,
        "runtime_config": KATA_RUNTIME_CONFIG,
        "qemu_binary": KATA_QEMU_BINARY,
        "virtiofsd": KATA_VIRTIOFS_BINARY,
        "guest_image": KATA_GUEST_IMAGE,
    }
    # The dedicated Docker daemon launches these root-owned host files.  The
    # readiness process may be the unprivileged operator and therefore cannot
    # use its own execute bit as a proxy for daemon access; the guest smoke is
    # the authoritative execution check.
    missing = [name for name, path in paths.items() if not path.is_file()]
    kvm = Path("/dev/kvm")
    kvm_present = kvm.exists()
    kvm_readable = os.access(kvm, os.R_OK)
    kvm_writable = os.access(kvm, os.W_OK)
    version = ""
    with contextlib.suppress(OSError):
        version = KATA_VERSION_FILE.read_text(encoding="utf-8").strip()
    result: dict[str, Any] = {
        "vmm": "qemu",
        "runtime_version": version or None,
        "runtime_shim": str(KATA_RUNTIME_SHIM),
        "runtime_config": str(KATA_RUNTIME_CONFIG),
        "qemu_binary": str(KATA_QEMU_BINARY),
        "guest_image": str(KATA_GUEST_IMAGE),
        "kvm_present": kvm_present,
        "kvm_readable": kvm_readable,
        "kvm_writable": kvm_writable,
        "missing_inputs": missing,
    }
    if missing:
        result.update(status="FAILED", reason="Kata runtime-rs/QEMU inputs are incomplete")
    elif not kvm_present:
        result.update(status="UNAVAILABLE_ON_THIS_HOST", reason="Kata QEMU requires usable /dev/kvm")
    else:
        # The AIAT Docker daemon is a root-owned host service.  The operator
        # shell may not have refreshed its supplementary kvm group yet; the
        # guest smoke below is the authoritative proof that the daemon can
        # actually open /dev/kvm.
        result["kvm_access"] = "direct" if kvm_readable and kvm_writable else "docker_daemon_root"
        result.update(status="READY", reason="Kata runtime-rs/QEMU inputs and /dev/kvm are ready for guest smoke")
    return result


def _run_kata_smoke(image: str, *, runtime: str = KATA_RUNTIME_NAME) -> tuple[bool, str, dict[str, Any]]:
    """Run a bounded Kata guest smoke and prove guest-kernel execution."""

    valid, reason = _validate_digest_image(image)
    if not valid:
        return False, reason, {}
    runtime_name = str(runtime or "").strip().lower()
    if not runtime_name or (runtime_name != "kata" and not runtime_name.startswith("kata-")):
        return False, "Kata smoke requires a Kata Docker runtime", {}
    name = f"aiat-kata-readiness-{uuid.uuid4().hex[:12]}"
    create = [
        *_docker_command(),
        "create",
        "--name",
        name,
        f"--runtime={runtime_name}",
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--pids-limit=64",
        "--memory=128m",
        "--cpus=0.25",
        image,
        "/bin/sh",
        "-c",
        "sleep 10",
    ]
    container_id = ""
    evidence: dict[str, Any] = {"runtime": runtime_name, "image": image}
    try:
        created = _run(create, timeout=60.0)
        if created.returncode != 0 or not created.stdout.strip():
            return False, "Kata guest container creation failed", evidence
        container_id = created.stdout.strip().splitlines()[-1]
        inspected = _run(
            [*_docker_command(), "inspect", "--format", "{{.HostConfig.Runtime}}", container_id],
            timeout=20.0,
        )
        runtime_seen = inspected.stdout.strip() if inspected.returncode == 0 else ""
        evidence["runtime_seen"] = runtime_seen
        if runtime_seen != runtime_name:
            return False, "Kata smoke did not retain the requested Docker runtime", evidence
        started = _run([*_docker_command(), "start", container_id], timeout=30.0)
        if started.returncode != 0:
            return False, "Kata guest container failed to start", evidence
        guest = _run([*_docker_command(), "exec", container_id, "/bin/uname", "-r"], timeout=30.0)
        guest_kernel = guest.stdout.strip() if guest.returncode == 0 else ""
        host_kernel = platform.release()
        evidence["guest_kernel"] = guest_kernel or None
        evidence["host_kernel"] = host_kernel
        if not guest_kernel or guest_kernel == host_kernel:
            return False, "Kata smoke did not prove a distinct guest kernel", evidence
        waited = _run([*_docker_command(), "wait", container_id], timeout=30.0)
        if waited.returncode != 0:
            return False, "Kata guest container did not complete cleanly", evidence
        return True, f"Kata ({runtime_name}) QEMU guest smoke completed", evidence
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"Kata smoke command unavailable ({type(exc).__name__})", evidence
    finally:
        if container_id:
            with contextlib.suppress(OSError, subprocess.TimeoutExpired):
                _run([*_docker_command(), "rm", "-f", container_id], timeout=20.0)


def inspect_live(
    *,
    smoke: bool = False,
    image: str | None = None,
    kata_image: str | None = None,
    require_firecracker: bool = False,
    require_kata: bool = False,
    check_kata: bool = False,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema_version": SANDBOX_SCHEMA,
        "mode": "live",
        "scope": "Docker runtime registration and selected sandbox smoke",
        "status": "blocked",
        "errors": [],
        "warnings": [],
        "sandbox_profile": "sandboxed",
        "sandbox_class": "sandboxed",
        "smoke": "not_checked",
        "kata_status": "NOT_SELECTED",
        "vm_isolated_status": "NOT_SELECTED",
        "kata": "not_selected",
        "vm_isolated": "not_selected",
        "kata_smoke": "not_checked",
        "firecracker": "superseded",
    }
    runtimes, error = _docker_runtimes()
    report["registered_runtimes"] = sorted(runtimes)
    if error:
        report["reason"] = error
        return report
    kata_runtimes = sorted(
        runtime for runtime in runtimes if runtime == "kata" or runtime.startswith("kata-")
    )
    report["kata_runtimes"] = kata_runtimes
    if check_kata or require_kata:
        if not kata_runtimes:
            state = "REQUIRED_UNAVAILABLE" if require_kata else "OPTIONAL_UNAVAILABLE"
            report["kata_status"] = state
            report["vm_isolated_status"] = state
            report["kata"] = state.lower()
            report["vm_isolated"] = state.lower()
            if require_kata:
                report["sandbox_profile"] = "vm_isolated"
                report["sandbox_class"] = "vm_isolated"
                report["reason"] = "Kata was required but no Kata Docker runtime is registered"
                return report
        else:
            selected_kata_runtime = kata_runtimes[0]
            prerequisites = _kata_host_prerequisites()
            report["kata_prerequisites"] = prerequisites
            if prerequisites.get("status") != "READY":
                state = (
                    "REQUIRED_UNAVAILABLE"
                    if require_kata and prerequisites.get("status") == "UNAVAILABLE_ON_THIS_HOST"
                    else "FAILED"
                    if prerequisites.get("status") == "FAILED"
                    else "OPTIONAL_UNAVAILABLE"
                )
                report["kata_status"] = state
                report["vm_isolated_status"] = state
                report["kata"] = state.lower()
                report["vm_isolated"] = state.lower()
                report["reason"] = str(prerequisites.get("reason", "Kata host prerequisites are unavailable"))
                if require_kata:
                    report["sandbox_profile"] = "vm_isolated"
                    report["sandbox_class"] = "vm_isolated"
                    return report
                if state == "FAILED":
                    report["warnings"].append(
                        str(report.get("reason", "Kata readiness failed; vm_isolated remains unavailable"))
                    )
            else:
                report["kata_vmm"] = str(prerequisites.get("vmm", "qemu"))
                kata_smoke_image = kata_image or image
                if not kata_smoke_image:
                    state = "REQUIRED_UNAVAILABLE" if require_kata else "OPTIONAL_UNAVAILABLE"
                    report["kata_status"] = state
                    report["vm_isolated_status"] = state
                    report["kata"] = state.lower()
                    report["vm_isolated"] = state.lower()
                    report["reason"] = "Kata readiness requires --kata-image or --image with an immutable digest"
                    if require_kata:
                        report["sandbox_profile"] = "vm_isolated"
                        report["sandbox_class"] = "vm_isolated"
                        return report
                else:
                    passed, kata_reason, kata_evidence = _run_kata_smoke(
                        kata_smoke_image,
                        runtime=selected_kata_runtime,
                    )
                    report["kata_smoke"] = "pass" if passed else "fail"
                    report["kata_smoke_evidence"] = kata_evidence
                    if passed:
                        report["kata_status"] = "AVAILABLE"
                        report["vm_isolated_status"] = "AVAILABLE"
                        report["kata"] = "available"
                        report["vm_isolated"] = "available"
                        report["kata_guest_kernel"] = kata_evidence.get("guest_kernel")
                    else:
                        report["kata_status"] = "FAILED"
                        report["vm_isolated_status"] = "FAILED"
                        report["kata"] = "failed"
                        report["vm_isolated"] = "failed"
                        report["reason"] = kata_reason
                        report["warnings"].append(kata_reason)
                    if require_kata:
                        report["sandbox_profile"] = "vm_isolated"
                        report["sandbox_class"] = "vm_isolated"
                        report["sandbox_runtime"] = selected_kata_runtime
                        report["smoke"] = "pass" if passed else "fail"
                        report["status"] = "pass" if passed else "fail"
                        return report

    if "runsc" not in runtimes:
        report["reason"] = "gVisor runsc runtime is not registered; no runc fallback is permitted"
        return report
    selected_runtime = "runsc"
    report["sandbox_runtime"] = selected_runtime
    if require_firecracker:
        # Kept solely for callers that explicitly request the historical
        # direct launcher. It is not part of the active development profile.
        firecracker = shutil.which("firecracker")
        report["firecracker"] = "available" if firecracker else "blocked_missing_binary"
        if firecracker is None:
            report["reason"] = "Firecracker was required but the binary is unavailable"
            return report
    if smoke:
        if not image:
            report["reason"] = "--smoke requires --image with an immutable digest"
            return report
        passed, reason = _run_smoke(image, runtime=selected_runtime)
        report["smoke"] = "pass" if passed else "fail"
        if not passed:
            report["status"] = "fail"
            report["reason"] = reason
            report["errors"] = [reason]
            return report
    report["status"] = "pass"
    report["reason"] = (
        f"{selected_runtime} is registered; sandbox smoke/canary/network negative evidence remains separate"
        if not smoke
        else reason
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers-dir", type=Path, default=DEFAULT_WORKERS_DIR)
    parser.add_argument("--compose", type=Path, default=DEFAULT_COMPOSE)
    parser.add_argument("--live", action="store_true", help="inspect the Docker runtime registry")
    parser.add_argument("--smoke", action="store_true", help="run a bounded smoke command using the selected runtime")
    parser.add_argument("--image", help="immutable OCI image for --smoke")
    parser.add_argument(
        "--kata-image",
        help="immutable OCI image for the Kata guest readiness smoke; defaults to --image",
    )
    parser.add_argument(
        "--require-firecracker",
        action="store_true",
        help="legacy explicit direct-launcher check; not part of the active profile",
    )
    parser.add_argument(
        "--check-kata",
        action="store_true",
        help="report optional Kata/vm_isolated availability without requiring it",
    )
    parser.add_argument(
        "--require-kata",
        "--require-vm-isolated",
        dest="require_kata",
        action="store_true",
        help="require a registered Kata Docker runtime and fail closed if absent",
    )
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
                kata_image=args.kata_image,
                require_firecracker=args.require_firecracker,
                require_kata=args.require_kata,
                check_kata=args.check_kata,
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
