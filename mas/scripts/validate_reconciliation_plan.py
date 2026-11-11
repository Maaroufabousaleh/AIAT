"""Validate a preservation-only migration manifest without accessing live data.

This guard never authorizes an import. Snapshot hashes do not establish row
identity or referential closure; those require separate source-backed checks.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

_CLASSES = {
    "operator_security_decision", "overlap_equal_common_columns",
    "semantic_reconciliation", "existing_native_preserve",
    "unknown_requires_decision", "historical_archive_candidate",
}
_SITUATIONS = {"desktop_only", "native_only", "same_key_equal", "same_key_conflict"}
_ACTIONS = {
    "preserve_and_block_import", "retain_both_snapshots",
    "preserve_and_review_relationships", "retain_unchanged",
    "preserve_read_only_pending_integrity_review",
}
_POLICIES = {
    ("desktop_only", "historical_archive_candidate", "preserve_read_only_pending_integrity_review"),
    ("desktop_only", "operator_security_decision", "preserve_and_block_import"),
    ("desktop_only", "semantic_reconciliation", "preserve_and_review_relationships"),
    ("desktop_only", "unknown_requires_decision", "preserve_and_block_import"),
    ("native_only", "existing_native_preserve", "retain_unchanged"),
    ("same_key_conflict", "operator_security_decision", "preserve_and_block_import"),
    ("same_key_equal", "overlap_equal_common_columns", "retain_both_snapshots"),
}


def validate(plan: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    if not isinstance(plan, dict):
        return {"valid": False, "errors": ["manifest_not_object"], "activation_authorized": False}
    if plan.get("schema_version") != 1 or plan.get("mode") != "read_only_plan_no_import":
        errors.append("unsupported_schema_or_mode")
    if plan.get("active_database_imports_authorized") != []:
        errors.append("active_import_not_empty")
    records = plan.get("records")
    if not isinstance(records, list) or not records:
        return {"valid": False, "errors": errors + ["records_missing"], "activation_authorized": False}
    seen: set[tuple[str, str, str]] = set()
    counts: Counter[str] = Counter()
    for index, row in enumerate(records):
        if not isinstance(row, dict):
            errors.append(f"record_{index}_invalid")
            continue
        table, key, situation = row.get("table"), row.get("key_hash"), row.get("situation")
        classification, action = row.get("classification"), row.get("action")
        if not isinstance(table, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", table):
            errors.append(f"record_{index}_table_invalid")
        if not isinstance(key, str) or not re.fullmatch(r"[0-9a-f]{32}", key):
            errors.append(f"record_{index}_key_invalid")
        if (not isinstance(situation, str) or not isinstance(classification, str)
                or not isinstance(action, str) or situation not in _SITUATIONS
                or classification not in _CLASSES or action not in _ACTIONS):
            errors.append(f"record_{index}_classification_invalid")
        elif (situation, classification, action) not in _POLICIES:
            errors.append(f"record_{index}_policy_mismatch")
        if row.get("automatic_write") is not False:
            errors.append(f"record_{index}_write_not_blocked")
        if all(isinstance(x, str) for x in (table, key, situation)):
            identity = (table, key, situation)
            if identity in seen:
                errors.append(f"record_{index}_duplicate")
            seen.add(identity)
        if isinstance(classification, str):
            counts[classification] += 1
    graphs = plan.get("foreign_key_graphs")
    if not isinstance(graphs, dict) or not all(isinstance(graphs.get(k), list) and graphs[k] for k in ("desktop_copy", "native_copy")):
        errors.append("foreign_key_graphs_missing")
    return {
        "valid": not errors, "errors": errors, "records": len(records),
        "classification_counts": dict(sorted(counts.items())),
        "activation_authorized": False,
        "referential_closure_verified": False,
        "limitation": "hashed snapshot keys do not prove row identity, ownership or dependency closure",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    try:
        result = validate(json.loads(args.manifest.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        result = {"valid": False, "errors": ["manifest_unreadable"], "activation_authorized": False}
    print(json.dumps(result, sort_keys=True))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
