"""Generate the shared flow-node schema artifact and dashboard form catalogue."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

MAS_ROOT = Path(__file__).resolve().parents[1]
JSON_OUTPUT = MAS_ROOT / "schemas" / "workflow" / "flow_nodes.v1.json"
TS_OUTPUT = MAS_ROOT / "apps" / "mas-dashboard" / "lib" / "generated" / "flow-node-schemas.ts"


def _catalog() -> dict[str, Any]:
    from mas_core.workflow import node_schema_catalog

    return node_schema_catalog()


def _typescript(catalog: dict[str, Any]) -> str:
    encoded = json.dumps(catalog, indent=2, sort_keys=True, ensure_ascii=True)
    return f"""/**
 * GENERATED FILE — do not edit by hand.
 * Source: mas_core.workflow.node_schema
 * Regenerate: uv run python scripts/generate_flow_node_schemas.py --write
 */

export const FLOW_NODE_SCHEMA_CATALOG = {encoded} as const;
export type FlowNodeSchemaCatalog = typeof FLOW_NODE_SCHEMA_CATALOG;
export type FlowNodeSchema = FlowNodeSchemaCatalog["node_types"][keyof FlowNodeSchemaCatalog["node_types"]];
export type FlowNodeFieldSchema = FlowNodeSchema["fields"][number];
"""


def _json_text(catalog: dict[str, Any]) -> str:
    return json.dumps(catalog, indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="write generated artifacts")
    parser.add_argument("--check", action="store_true", help="fail when artifacts differ")
    args = parser.parse_args(argv)

    try:
        catalog = _catalog()
        outputs = {JSON_OUTPUT: _json_text(catalog), TS_OUTPUT: _typescript(catalog)}
    except (ImportError, OSError, TypeError, ValueError) as exc:
        print(f"flow-node-schemas: unable to generate: {exc}", file=sys.stderr)
        return 1

    stale: list[Path] = []
    for path, content in outputs.items():
        existing = path.read_text(encoding="utf-8") if path.exists() else None
        if args.write:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            existing = content
        if args.check and existing != content:
            stale.append(path)
    if stale:
        print("flow-node-schemas: generated output is stale: " + ", ".join(str(path) for path in stale), file=sys.stderr)
        return 1
    print(
        f"flow-node-schemas: {'pass' if args.check else 'generated'}; "
        f"version={catalog['schema_version']}, node_types={len(catalog['node_types'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
