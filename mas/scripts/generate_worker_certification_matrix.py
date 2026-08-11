"""Generate the deterministic default-worker certification matrix.

The matrix is a declaration/evidence inventory, not a certification claim. It
keeps exact adapter/runtime inputs visible and records whether a worker still
needs operational, security, or live canary evidence. Environment-dependent
package availability and Docker/live results remain in the companion checker
reports and are never baked into this generated file.
"""

from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from mas_core.protocols.worker_manifest import WorkerManifest
from mas_core.worker_registry.runtime_catalog import RUNTIME_CATALOG

MAS_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORKERS_DIR = MAS_ROOT / "workers"
DEFAULT_OUTPUT = MAS_ROOT / "docs" / "provenance" / "worker_certification_matrix.yaml"


def _load_manifest(path: Path) -> WorkerManifest:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a mapping")
    return WorkerManifest.model_validate(value)


def _security_status(manifest: WorkerManifest) -> str:
    return str((manifest.source_provenance or {}).get("security_scan_status") or "not_recorded")


def _evidence_state(manifest: WorkerManifest) -> str:
    certification = manifest.certification_status
    source_repo = str(manifest.metadata.source_repo or "").strip().lower()
    security = _security_status(manifest).lower()
    if certification == "blocked":
        return "blocked"
    if source_repo not in {"", "local"} and security != "passed":
        return "pending_security_evidence"
    if certification in {"certified", "approved"}:
        return "declared_certified_live_retest_required"
    return "pending_live_certification"


def generate(*, workers_dir: Path = DEFAULT_WORKERS_DIR) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for path in sorted(workers_dir.glob("*.yaml")):
        try:
            manifest = _load_manifest(path)
        except (OSError, ValueError, yaml.YAMLError, ValidationError) as exc:
            errors.append(f"{path.name}: {exc}")
            continue
        runtime = RUNTIME_CATALOG.get(manifest.runtime_tier)
        if runtime is None:
            errors.append(f"{path.name}: unknown runtime tier {manifest.runtime_tier!r}")
            required_imports: list[str] = []
        else:
            required_imports = list(runtime.required_imports)
        rows.append(
            {
                "worker_id": manifest.metadata.id,
                "source_repo": manifest.metadata.source_repo or "local",
                "source_revision": manifest.metadata.source_revision,
                "version_pin": manifest.metadata.version_pin,
                "runtime_tier": manifest.runtime_tier,
                "required_imports": required_imports,
                "transport": manifest.runtime.transport,
                "isolation_mode": manifest.integration.isolation_mode,
                "adapter_entrypoint": manifest.integration.adapter_entrypoint,
                "adapter_version": manifest.integration.certified_adapter_version,
                "certification_status": manifest.certification_status,
                "security_scan_status": _security_status(manifest),
                "steward_id": str(manifest.steward_id or manifest.integration.steward_id or "") or None,
                "evidence_state": _evidence_state(manifest),
            }
        )
    rows.sort(key=lambda row: row["worker_id"])
    return {
        "schema_version": "aiat.worker-certification-matrix.v1",
        "programme_scope": "personal-internal-only",
        "license_handling": "metadata-only",
        "workers": rows,
        "errors": sorted(errors),
    }


def _render(value: dict[str, Any]) -> str:
    return yaml.safe_dump(value, sort_keys=False, allow_unicode=False, width=120)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers-dir", type=Path, default=DEFAULT_WORKERS_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--write", action="store_true", help="write the generated matrix")
    parser.add_argument("--check", action="store_true", help="fail if the checked-in matrix is stale")
    args = parser.parse_args(argv)
    matrix = generate(workers_dir=args.workers_dir)
    if matrix["errors"]:
        for error in matrix["errors"]:
            print(f"worker-matrix: error: {error}", file=sys.stderr)
        return 1
    rendered = _render(matrix)
    if args.write:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    if args.check:
        if not args.output.exists():
            print(f"worker-matrix: missing generated artifact {args.output}", file=sys.stderr)
            return 1
        current = args.output.read_text(encoding="utf-8")
        if current != rendered:
            diff = difflib.unified_diff(
                current.splitlines(),
                rendered.splitlines(),
                fromfile=str(args.output),
                tofile="generated",
                lineterm="",
            )
            print("\n".join(diff), file=sys.stderr)
            return 1
    print(
        "worker-matrix: "
        f"status=pass workers={len(matrix['workers'])} "
        f"pending_security={sum(row['evidence_state'] == 'pending_security_evidence' for row in matrix['workers'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
