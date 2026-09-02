"""Reconcile worker declarations with installed runtime prerequisites.

The report is intentionally narrower than certification: an available Python
package is not a security scan, sandbox proof, canary, live run, or rollback
result. ``--live`` performs the environment import probe and exits 2 when a
required runtime used by a worker is unavailable. Licence/restriction fields
are not read by this check.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from mas_core.protocols.worker_manifest import WorkerManifest
from mas_core.worker_registry.runtime_catalog import RUNTIME_CATALOG

MAS_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORKERS_DIR = MAS_ROOT / "workers"
READINESS_SCHEMA = "aiat.worker-runtime-readiness.v1"


def _package_available(import_name: str) -> bool:
    try:
        return importlib.util.find_spec(import_name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def _load_manifests(workers_dir: Path) -> tuple[list[WorkerManifest], list[str]]:
    manifests: list[WorkerManifest] = []
    errors: list[str] = []
    for path in sorted(workers_dir.glob("*.yaml")):
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            manifests.append(WorkerManifest.model_validate(raw))
        except (OSError, ValueError, yaml.YAMLError, ValidationError) as exc:
            errors.append(f"{path.name}: {exc}")
    return manifests, errors


def reconcile(
    *,
    workers_dir: Path = DEFAULT_WORKERS_DIR,
    live: bool = False,
    package_availability: dict[str, bool] | None = None,
) -> dict[str, Any]:
    manifests, errors = _load_manifests(workers_dir)
    worker_counts = Counter(manifest.runtime_tier for manifest in manifests)
    rows: list[dict[str, Any]] = []
    for runtime_id, definition in sorted(RUNTIME_CATALOG.items()):
        missing = [
            package
            for package in definition.required_imports
            if not (
                package_availability.get(package, False)
                if package_availability is not None
                else _package_available(package)
            )
        ]
        worker_count = worker_counts.get(runtime_id, 0)
        if not live:
            status = "declared"
        elif missing:
            status = "blocked_missing_runtime"
        elif runtime_id == "external":
            status = "external_adapter_evidence_required"
        else:
            status = "runtime_imports_available"
        rows.append(
            {
                "runtime_id": runtime_id,
                "worker_count": worker_count,
                "required_imports": list(definition.required_imports),
                "missing_imports": missing,
                "optional": definition.optional,
                "supported_transports": list(definition.supported_transports),
                "supported_isolation_modes": list(definition.supported_isolation_modes),
                "status": status,
            }
        )
    required_missing = [
        row
        for row in rows
        if row["worker_count"] > 0 and row["missing_imports"] and not row["optional"]
    ]
    status = "pass"
    reason: str | None = None
    if errors:
        status = "blocked"
        reason = "worker manifest validation failed"
    elif live and required_missing:
        status = "blocked"
        reason = "required runtime package imports are unavailable"
    return {
        "schema_version": READINESS_SCHEMA,
        "mode": "live" if live else "static",
        "status": status,
        "reason": reason,
        "worker_count": len(manifests),
        "runtime_count": len(rows),
        "required_runtime_blockers": required_missing,
        "errors": errors,
        "runtimes": rows,
        "certification_boundary": {
            "package_imports": "checked" if live else "declared_only",
            "security_scan": "not_checked",
            "sandbox": "not_checked",
            "canary": "not_checked",
            "live_worker_run": "not_checked",
            "rollback": "not_checked",
        },
    }


def _compose_import_probe(
    *,
    required_imports: tuple[str, ...],
    container: str,
) -> tuple[dict[str, bool], dict[str, Any] | None]:
    """Probe imports in the running orchestrator image, not the host venv."""
    if shutil.which("docker") is None:
        return {}, {"status": "blocked", "reason": "Docker CLI is unavailable for the Compose runtime probe"}
    code = (
        "import importlib.util, json; "
        f"names={json.dumps(list(required_imports), sort_keys=True)}; "
        "print(json.dumps({name: importlib.util.find_spec(name) is not None for name in names}, sort_keys=True))"
    )
    try:
        result = subprocess.run(
            ["docker", "exec", container, "python", "-c", code],
            cwd=MAS_ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=30.0,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {}, {"status": "blocked", "reason": f"Compose runtime probe unavailable: {type(exc).__name__}"}
    if result.returncode != 0:
        return {}, {"status": "blocked", "reason": "orchestrator container runtime probe failed"}
    try:
        payload = json.loads(result.stdout.strip())
    except json.JSONDecodeError:
        return {}, {"status": "blocked", "reason": "orchestrator container runtime probe returned invalid JSON"}
    if not isinstance(payload, dict) or any(
        not isinstance(name, str) or not isinstance(value, bool)
        for name, value in payload.items()
    ):
        return {}, {"status": "blocked", "reason": "orchestrator container runtime probe returned an invalid report"}
    availability = {name: bool(payload.get(name, False)) for name in required_imports}
    return availability, None


def compose_local_reconcile(
    *,
    workers_dir: Path = DEFAULT_WORKERS_DIR,
    container: str = "mas-orchestrator-api-1",
) -> dict[str, Any]:
    """Reconcile runtime imports inside the local orchestrator container."""
    required_imports = tuple(
        sorted(
            {
                package
                for definition in RUNTIME_CATALOG.values()
                for package in definition.required_imports
            }
        )
    )
    availability, error = _compose_import_probe(
        required_imports=required_imports,
        container=container,
    )
    if error:
        static = reconcile(workers_dir=workers_dir, live=False)
        return {
            **static,
            "mode": "live",
            "status": "blocked",
            "reason": error["reason"],
            "environment": "compose-local",
            "runtime_probe": {"container": container, "transport": "docker-exec", **error},
        }
    report = reconcile(
        workers_dir=workers_dir,
        live=True,
        package_availability=availability,
    )
    report["environment"] = "compose-local"
    report["runtime_probe"] = {
        "container": container,
        "transport": "docker-exec",
        "status": "pass",
        "imports": availability,
    }
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers-dir", type=Path, default=DEFAULT_WORKERS_DIR)
    parser.add_argument("--live", action="store_true", help="probe installed runtime imports")
    parser.add_argument(
        "--compose-local",
        action="store_true",
        help="probe imports inside the local orchestrator container",
    )
    parser.add_argument(
        "--container",
        default="mas-orchestrator-api-1",
        help="orchestrator container name for --compose-local",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args(argv)
    if args.compose_local and not args.live:
        report = {
            "schema_version": READINESS_SCHEMA,
            "mode": "live",
            "status": "blocked",
            "reason": "--compose-local requires --live",
        }
    elif args.compose_local:
        report = compose_local_reconcile(workers_dir=args.workers_dir, container=args.container)
    else:
        report = reconcile(workers_dir=args.workers_dir, live=args.live)
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        print(
            "worker runtime readiness: "
            f"status={report['status']} mode={report['mode']} "
            f"workers={report['worker_count']} blockers={len(report['required_runtime_blockers'])}"
        )
        if report["reason"]:
            print(f"reason: {report['reason']}")
    return 2 if report["status"] == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
