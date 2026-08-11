"""Validate the maintained AIAT documentation authority and local links.

This is a repository-only check. It verifies that the root roadmap references
the canonical target, current feature specifications, and ordered plans; all
local links in the maintained documents resolve; and the personal/internal
metadata-only licence policy is stated consistently. It never evaluates or
blocks an OSS resource based on its licence metadata.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

MAS_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = MAS_ROOT.parent
CHECK_SCHEMA = "aiat.docs-index-check.v1"
LINK_RE = re.compile(r"\[[^\]]*\]\((?:<([^>]+)>|([^)]*))\)")

MAINTAINED_DOCS = (
    REPO_ROOT / "AIAT_TARGET_PROGRAMME.md",
    REPO_ROOT / "ROADMAP.md",
    REPO_ROOT / "THIRD_PARTY_NOTICES.md",
    REPO_ROOT / "Docs" / "current" / "P0_RELEASE_INTEGRITY_STATUS.md",
    MAS_ROOT / "docs" / "AIAT_CURRENT_RELEASE_LEDGER.md",
    MAS_ROOT / "docs" / "P0_NATIVE_LINUX_EXIT_RUNBOOK.md",
)
REQUIRED_POLICY_MARKERS = {
    REPO_ROOT / "THIRD_PARTY_NOTICES.md": (
        "AIAT has no licence allowlist",
        "not an automated hiring, activation, installation, update, execution, or release gate",
    ),
    MAS_ROOT / "docs" / "provenance" / "third_party_components.yaml": (
        "license_handling: metadata-only",
        "enforce_license_gate: false",
    ),
    REPO_ROOT / "ROADMAP.md": (
        "licence data is informational metadata and never an activation gate",
    ),
}


def _canonical_docs() -> list[Path]:
    feature_docs = sorted((REPO_ROOT / "Docs" / "current").glob("FEATURE_*.md"))
    plans = sorted((REPO_ROOT / "Docs" / "current" / "plans").glob("*_PLAN.md"))
    return [*feature_docs, *plans]


def _link_target(source: Path, raw: str) -> Path | None:
    target = raw.strip()
    if not target or target.startswith(("#", "http://", "https://", "mailto:")):
        return None
    target = target.split("#", 1)[0].split("?", 1)[0]
    if not target:
        return None
    return (source.parent / target).resolve()


def _local_link_errors(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"{path}: cannot read maintained document ({type(exc).__name__})"]
    errors: list[str] = []
    for match in LINK_RE.finditer(text):
        raw = match.group(1) or match.group(2) or ""
        target = _link_target(path, raw)
        if target is not None and not target.exists():
            errors.append(f"{path.relative_to(REPO_ROOT)}: missing local link {raw}")
    return errors


def _policy_errors() -> list[str]:
    errors: list[str] = []
    for path, markers in REQUIRED_POLICY_MARKERS.items():
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            errors.append(f"{path}: cannot read policy source ({type(exc).__name__})")
            continue
        normalized = " ".join(text.split())
        for marker in markers:
            if marker not in text and marker not in normalized:
                errors.append(f"{path.relative_to(REPO_ROOT)}: missing policy marker {marker!r}")
    return errors


def _roadmap_reference_errors(canonical: list[Path]) -> list[str]:
    roadmap = REPO_ROOT / "ROADMAP.md"
    try:
        text = roadmap.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"ROADMAP.md: cannot read ({type(exc).__name__})"]
    errors: list[str] = []
    for path in canonical:
        relative = path.relative_to(REPO_ROOT).as_posix()
        if relative not in text:
            errors.append(f"ROADMAP.md: canonical document is not referenced: {relative}")
    for required in ("AIAT_TARGET_PROGRAMME.md", "THIRD_PARTY_NOTICES.md", "P0_RELEASE_INTEGRITY_PLAN.md"):
        if required not in text:
            errors.append(f"ROADMAP.md: required authority reference is missing: {required}")
    return errors


def build_report() -> dict[str, Any]:
    canonical = _canonical_docs()
    maintained = [path for path in (*MAINTAINED_DOCS, *canonical) if path.exists()]
    errors: list[str] = []
    missing_maintained = [str(path.relative_to(REPO_ROOT)) for path in (*MAINTAINED_DOCS, *canonical) if not path.exists()]
    errors.extend(f"maintained document is missing: {path}" for path in missing_maintained)
    for path in maintained:
        if path.suffix.lower() == ".md":
            errors.extend(_local_link_errors(path))
    errors.extend(_policy_errors())
    errors.extend(_roadmap_reference_errors(canonical))
    return {
        "schema_version": CHECK_SCHEMA,
        "status": "fail" if errors else "pass",
        "canonical_feature_count": sum(path.name.startswith("FEATURE_") for path in canonical),
        "canonical_plan_count": sum(path.name.endswith("_PLAN.md") for path in canonical),
        "maintained_document_count": len(maintained),
        "link_checked_document_count": sum(path.suffix.lower() == ".md" for path in maintained),
        "errors": errors,
        "policy": {
            "programme_scope": "personal-internal-only",
            "licence_metadata_is_gate": False,
        },
        "scope": "maintained documentation links, roadmap references, and policy markers only",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args(argv)
    report = build_report()
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        print(
            f"docs-index: {report['status']} — "
            f"features={report['canonical_feature_count']} plans={report['canonical_plan_count']}"
        )
        for error in report["errors"]:
            print(f"docs-index: {error}", file=sys.stderr)
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
