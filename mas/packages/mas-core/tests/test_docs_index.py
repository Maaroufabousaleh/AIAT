"""Tests for the maintained documentation authority check."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "check_docs_index.py"


def test_link_target_normalization_does_not_dereference_symlinks() -> None:
    namespace: dict[str, object] = {
        "__name__": "check_docs_index_test",
        "__file__": str(SCRIPT),
    }
    exec(SCRIPT.read_text(encoding="utf-8"), namespace)
    link_target = namespace["_link_target"]
    source = Path("/tmp/aiat-docs/source.md")

    target = link_target(source, "../docs/target.md")

    assert target == Path(os.path.normpath("/tmp/aiat-docs/../docs/target.md"))


def test_docs_index_has_one_target_thirteen_features_and_three_plans() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--json"],
        cwd=SCRIPT.parents[1],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["schema_version"] == "aiat.docs-index-check.v1"
    assert report["status"] == "pass"
    assert report["canonical_feature_count"] == 13
    assert report["canonical_plan_count"] == 3
    assert report["policy"] == {
        "licence_metadata_is_gate": False,
        "licence_detail_surface": "metadata-only",
        "licence_metadata_surfaces": [
            "THIRD_PARTY_NOTICES.md",
            "mas/docs/provenance/third_party_components.yaml",
        ],
        "programme_scope": "personal-internal-only",
    }
