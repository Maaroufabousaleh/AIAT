from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

MAS_ROOT = Path(__file__).resolve().parents[3]
GENERATOR = MAS_ROOT / "scripts" / "generate_flow_node_schemas.py"
JSON_ARTIFACT = MAS_ROOT / "schemas" / "workflow" / "flow_nodes.v1.json"
TS_ARTIFACT = MAS_ROOT / "apps" / "mas-dashboard" / "lib" / "generated" / "flow-node-schemas.ts"


def test_checked_in_flow_node_schema_artifacts_are_reproducible() -> None:
    result = subprocess.run(
        [sys.executable, str(GENERATOR), "--check"],
        cwd=MAS_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert JSON_ARTIFACT.exists()
    assert TS_ARTIFACT.exists()

    catalog = json.loads(JSON_ARTIFACT.read_text(encoding="utf-8"))
    assert catalog["catalog_id"] == "aiat.flow-node-schemas"
    assert catalog["schema_version"] == "1.0"
    assert "FLOW_NODE_SCHEMA_CATALOG" in TS_ARTIFACT.read_text(encoding="utf-8")
