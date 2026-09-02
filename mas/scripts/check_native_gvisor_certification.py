"""Run the native-Linux gVisor certification boundary.

This command is intended for a disposable, operator-triggered Linux CI or
release host.  It refuses WSL kernels, Docker engines without a registered
``runsc`` runtime, and mutable smoke images.  Only bounded host/runtime facts,
exit statuses, and cleanup counts are written to the evidence report; command
stdout/stderr and container payloads are deliberately not retained.

The report is evidence, not a release decision.  A later reviewed ledger
commit may reference a passing artifact after the complete native release
suite has been run on the same candidate revision.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

SCHEMA = "aiat.native-gvisor-certification.v1"
MAS_ROOT = Path(__file__).resolve().parents[1]
MAX_CAPTURED_TEXT = 256
DIGEST_IMAGE_RE = re.compile(r"^[^\s@]+@sha256:[0-9a-fA-F]{64}$")


def _bounded_text(value: str | None) -> str:
    text = (value or "").strip().replace("\x00", "")
    return text[:MAX_CAPTURED_TEXT]


def _run(command: list[str], *, timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=MAS_ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _command_result(
    command: list[str],
    *,
    timeout: float = 30.0,
    record_output: bool = False,
) -> dict[str, Any]:
    try:
        result = _run(command, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "command": command[:4],
            "status": "blocked",
            "returncode": None,
            "error_type": type(exc).__name__,
        }
    row: dict[str, Any] = {
        "command": command[:4],
        "status": "pass" if result.returncode == 0 else "fail",
        "returncode": result.returncode,
    }
    if record_output:
        row["bounded_output"] = _bounded_text(result.stdout or result.stderr)
    return row


def _git_revision() -> str | None:
    result = _command_result(["git", "rev-parse", "HEAD"], record_output=True)
    if result.get("status") != "pass":
        return None
    value = str(result.get("bounded_output") or "").strip()
    return value if len(value) == 40 else None


def _docker_json(format_string: str) -> tuple[dict[str, Any] | list[Any] | None, str | None]:
    if shutil.which("docker") is None:
        return None, "docker_cli_unavailable"
    try:
        result = _run(["docker", "info", "--format", format_string], timeout=20.0)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, type(exc).__name__
    if result.returncode != 0:
        return None, "docker_engine_unavailable"
    try:
        value = json.loads(result.stdout.strip())
    except json.JSONDecodeError:
        return None, "docker_metadata_invalid"
    if not isinstance(value, (dict, list)):
        return None, "docker_metadata_shape_invalid"
    return value, None


def _repo_digest(image: str) -> str | None:
    normalized = image.strip()
    return normalized if DIGEST_IMAGE_RE.fullmatch(normalized) else None


def _remaining_named_containers(name: str) -> int | None:
    try:
        result = _run(["docker", "ps", "-aq", "--filter", f"name=^{name}$"], timeout=15.0)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return len([line for line in result.stdout.splitlines() if line.strip()])


def _run_sandbox_suite(smoke_image: str) -> dict[str, Any]:
    command = [
        sys.executable,
        str(Path(__file__).with_name("check_sandbox_runtime_readiness.py")),
        "--live",
        "--smoke",
        "--image",
        smoke_image,
        "--json",
    ]
    try:
        result = _run(command, timeout=120.0)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"status": "blocked", "error_type": type(exc).__name__}
    try:
        payload = json.loads(result.stdout.strip())
    except json.JSONDecodeError:
        return {
            "status": "fail" if result.returncode else "blocked",
            "returncode": result.returncode,
            "reason": "sandbox suite returned non-JSON output",
        }
    if not isinstance(payload, dict):
        return {"status": "fail", "returncode": result.returncode, "reason": "sandbox suite shape invalid"}
    live = payload.get("live")
    live_status = live.get("status") if isinstance(live, dict) else None
    return {
        "status": live_status if live_status in {"pass", "fail", "blocked"} else "fail",
        "returncode": result.returncode,
        "registered_runtimes": live.get("registered_runtimes", []) if isinstance(live, dict) else [],
        "smoke": live.get("smoke") if isinstance(live, dict) else None,
        "reason": live.get("reason") if isinstance(live, dict) else "sandbox suite did not return live evidence",
    }


def certify(*, smoke_image: str, hello_image: str | None = None) -> dict[str, Any]:
    """Collect native host, runtime, smoke, suite, and cleanup evidence."""

    blockers: list[str] = []
    failures: list[str] = []
    hello_image = hello_image or smoke_image
    system = platform.system()
    kernel = platform.release()
    native_linux = system == "Linux" and not any(marker in kernel.lower() for marker in ("microsoft", "wsl"))
    if not native_linux:
        blockers.append("host is not a native Linux release host")

    runsc = shutil.which("runsc")
    runsc_version: dict[str, Any]
    if runsc is None:
        blockers.append("runsc binary is unavailable")
        runsc_version = {"status": "blocked", "reason": "runsc binary unavailable"}
    else:
        runsc_version = _command_result([runsc, "--version"], record_output=True)
        if runsc_version.get("status") != "pass":
            blockers.append("runsc version probe failed")

    runtimes, docker_error = _docker_json("{{json .Runtimes}}")
    runtime_names = sorted(str(name) for name in runtimes) if isinstance(runtimes, dict) else []
    if docker_error:
        blockers.append(docker_error)
    elif "runsc" not in runtime_names:
        blockers.append("runsc is not registered with the Docker engine")

    smoke_digest = _repo_digest(smoke_image)
    if smoke_digest is None:
        blockers.append("smoke image must be an immutable digest reference")

    hello_digest = _repo_digest(hello_image)
    if hello_digest is None:
        blockers.append("hello image must be an immutable digest reference")
    if smoke_digest is None or hello_digest is None:
        # Never execute a mutable image merely because another prerequisite
        # already blocked the run.  This keeps the failure fail-closed and
        # prevents a floating tag from entering certification evidence.
        hello = {"status": "blocked", "reason": "immutable image input required"}
        dmesg = {"status": "blocked", "reason": "immutable image input required"}
        suite = {"status": "blocked", "reason": "immutable image input required"}
    else:
        hello = _command_result(
            ["docker", "run", "--rm", "--runtime=runsc", hello_digest],
            timeout=120.0,
        )
        if hello.get("status") != "pass":
            failures.append("hello-world runsc smoke failed")

        dmesg = _command_result(
            ["docker", "run", "--rm", "--runtime=runsc", smoke_digest, "dmesg"],
            timeout=120.0,
            record_output=True,
        )
        if dmesg.get("status") != "pass":
            failures.append("gVisor dmesg identification command failed")
        elif "Starting gVisor" not in str(dmesg.get("bounded_output") or ""):
            failures.append("gVisor dmesg identification marker was absent")

        suite = _run_sandbox_suite(smoke_digest)
        if suite["status"] == "blocked":
            blockers.append(str(suite.get("reason") or "sandbox suite blocked"))
        elif suite["status"] != "pass":
            failures.append(str(suite.get("reason") or "sandbox suite failed"))

    cleanup_name = f"aiat-gvisor-cert-{os.getenv('GITHUB_RUN_ID', 'local')}"
    cleanup_count = _remaining_named_containers(cleanup_name)
    if cleanup_count not in (0, None):
        failures.append("named certification containers remain")

    if blockers:
        status = "blocked"
    elif failures:
        status = "fail"
    else:
        status = "pass"
    return {
        "schema_version": SCHEMA,
        "status": status,
        "candidate_commit": _git_revision(),
        "host": {
            "system": system,
            "kernel": kernel,
            "platform": platform.platform(aliased=True),
            "architecture": platform.machine(),
            "native_linux": native_linux,
            "github_runner": bool(os.getenv("GITHUB_ACTIONS")),
        },
        "docker": {
            "runtime_names": runtime_names,
            "server_probe": "pass" if docker_error is None else "blocked",
            "error": docker_error,
        },
        "runsc": {"path_present": runsc is not None, "version": runsc_version},
        "images": {
            "hello_world_digest": hello_digest,
            "smoke_image_digest": smoke_digest,
        },
        "checks": {
            "hello_world_runsc": {key: value for key, value in hello.items() if key != "command"},
            "dmesg_identification": {key: value for key, value in dmesg.items() if key != "command"},
            "sandbox_suite": suite,
            "cleanup": {
                "named_container_residue": cleanup_count,
                "zero_residue_verified": cleanup_count == 0,
            },
        },
        "blockers": blockers,
        "failures": failures,
        "scope": "native-Linux gVisor runtime and bounded smoke evidence; no release decision",
        "licence_metadata_is_gate": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smoke-image", required=True, help="image reference; must resolve to an OCI digest")
    parser.add_argument(
        "--hello-image",
        help="optional second immutable OCI image; defaults to the smoke-image digest",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = certify(smoke_image=args.smoke_image, hello_image=args.hello_image)
    serialized = json.dumps(report, sort_keys=True, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding="utf-8")
    if args.json or not args.output:
        print(serialized, end="")
    return 0 if report["status"] == "pass" else 2 if report["status"] == "blocked" else 1


if __name__ == "__main__":
    raise SystemExit(main())
