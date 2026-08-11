"""Validate the code-review adapter catalogue and default worker binding.

The local deterministic reviewer is the reproducible default.  External
candidate names remain selectable only after an operator records an exact
repository/revision/version and representative evidence; this check does not
resolve repositories or run review commands.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

CHECK_SCHEMA = "aiat.code-review-adapter-check.v1"
ROOT = Path(__file__).resolve().parents[1]
CATALOGUE = ROOT / "docs" / "provenance" / "code_review_adapters.yaml"
WORKER = ROOT / "workers" / "code_review_worker.yaml"


def build_report() -> dict[str, Any]:
    errors: list[str] = []
    catalogue = yaml.safe_load(CATALOGUE.read_text(encoding="utf-8")) or {}
    worker = yaml.safe_load(WORKER.read_text(encoding="utf-8")) or {}
    if catalogue.get("schema_version") != "aiat.code-review-adapters.v1":
        errors.append("catalogue schema_version is invalid")
    policy = catalogue.get("policy") or {}
    if policy.get("licence_metadata_is_gate") is not False:
        errors.append("licence metadata must be explicitly non-gating")
    adapters = catalogue.get("adapters") or []
    by_id = {str(item.get("id")): item for item in adapters if isinstance(item, dict)}
    default_id = ((worker.get("runtime") or {}).get("adapter_config") or {}).get("default_adapter")
    if default_id != "aiat_deterministic_diff_review":
        errors.append("code_review_worker must select the deterministic local default")
    default = by_id.get(str(default_id))
    if not isinstance(default, dict):
        errors.append("deterministic local default is missing from the catalogue")
    else:
        for key in ("source_repo", "source_revision", "version", "entrypoint"):
            if not default.get(key):
                errors.append(f"local default is missing {key}")
        if default.get("status") != "available":
            errors.append("local default must be available")

    external_status: dict[str, str] = {}
    for item in adapters:
        if not isinstance(item, dict) or item.get("kind") != "external":
            continue
        adapter_id = str(item.get("id") or "unknown")
        status = str(item.get("status") or "")
        external_status[adapter_id] = status
        if status == "available" and not all(item.get(key) for key in ("source_repo", "source_revision", "version")):
            errors.append(f"external adapter {adapter_id} cannot be available without exact source/revision/version")

    configured_external = set(
        str(item)
        for item in (((worker.get("runtime") or {}).get("adapter_config") or {}).get("optional_external_adapters") or [])
    )
    if configured_external != set(external_status):
        errors.append("worker optional_external_adapters must match catalogue external IDs")
    return {
        "schema_version": CHECK_SCHEMA,
        "status": "pass" if not errors else "fail",
        "default_adapter": default_id,
        "external_adapter_status": dict(sorted(external_status.items())),
        "external_activation_blocked": any(status != "available" for status in external_status.values()),
        "errors": errors,
        "review_execution_performed": False,
        "network_access_performed": False,
        "licence_metadata_is_gate": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args(argv)
    report = build_report()
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        print(f"code-review adapters: {report['status']}")
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
