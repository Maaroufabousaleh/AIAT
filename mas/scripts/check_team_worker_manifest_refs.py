"""Check explicit team-runner to worker-manifest identity bindings.

This is a static/read-only declaration check.  It does not infer a missing
reference from an agent name, register a worker with the control plane, or
activate/provision any runtime.  Licence and resource-restriction metadata are
not evaluated by the identity check.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from mas_core.worker_registry.team_manifest_refs import (
    TEAM_MANIFEST_REFS_SCHEMA,
    reconcile_team_worker_manifest_refs,
)

MAS_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TEAMS_DIR = MAS_ROOT / "teams"
DEFAULT_WORKERS_DIR = MAS_ROOT / "workers"
CHECK_SCHEMA = "aiat.team-worker-manifest-refs-check.v1"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--teams-dir", type=Path, default=DEFAULT_TEAMS_DIR)
    parser.add_argument("--workers-dir", type=Path, default=DEFAULT_WORKERS_DIR)
    parser.add_argument("--json", action="store_true", help="emit the machine-readable report")
    args = parser.parse_args(argv)
    reconciliation = reconcile_team_worker_manifest_refs(
        teams_dir=args.teams_dir,
        workers_dir=args.workers_dir,
    )
    report = {
        "schema_version": CHECK_SCHEMA,
        "reconciliation_schema": TEAM_MANIFEST_REFS_SCHEMA,
        "status": reconciliation["status"],
        "licence_metadata_is_gate": False,
        "no_mutation": True,
        "reconciliation": reconciliation,
        "scope": "read-only team-runner declaration check; no worker registration, activation, provisioning, or licence decision",
    }
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        print(
            f"team worker manifest refs: {report['status']} — "
            f"teams={reconciliation['team_count']} agents={reconciliation['agent_count']} "
            f"errors={len(reconciliation['errors'])}"
        )
        for error in reconciliation["errors"]:
            print(f"team worker manifest refs: {error}", file=sys.stderr)
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
