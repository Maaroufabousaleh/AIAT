"""Deterministic portable flow-definition hashing and review diffs."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def flow_definition_hash(definition_json: dict[str, Any]) -> str:
    """Return a stable digest for an exported flow definition."""

    return hashlib.sha256(
        json.dumps(definition_json, sort_keys=True, separators=(",", ":"), default=str).encode(
            "utf-8"
        )
    ).hexdigest()


def flow_definition_diff(
    from_definition: dict[str, Any], to_definition: dict[str, Any]
) -> dict[str, Any]:
    """Build a deterministic node/edge/metadata diff for operator review."""

    def keyed(items: Any, key: str) -> dict[str, Any]:
        if not isinstance(items, list):
            return {}
        return {
            str(item.get(key)): item
            for item in items
            if isinstance(item, dict) and item.get(key) is not None
        }

    def diff_items(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
        before_keys = set(before)
        after_keys = set(after)
        changed = [
            {"id": key, "before": before[key], "after": after[key]}
            for key in sorted(before_keys & after_keys)
            if before[key] != after[key]
        ]
        return {
            "added": [after[key] for key in sorted(after_keys - before_keys)],
            "removed": [before[key] for key in sorted(before_keys - after_keys)],
            "changed": changed,
        }

    return {
        "schema_version": {
            "from": from_definition.get("schema_version", "1.0"),
            "to": to_definition.get("schema_version", "1.0"),
        },
        "nodes": diff_items(keyed(from_definition.get("nodes"), "id"), keyed(to_definition.get("nodes"), "id")),
        "edges": diff_items(keyed(from_definition.get("edges"), "id"), keyed(to_definition.get("edges"), "id")),
        "metadata_changed": (from_definition.get("metadata") or {})
        != (to_definition.get("metadata") or {}),
    }
