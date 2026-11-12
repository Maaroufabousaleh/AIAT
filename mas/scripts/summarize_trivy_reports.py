"""Summarize Trivy JSON reports for auditable candidate evidence."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


def summarize_report(path: Path) -> dict[str, Any]:
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Trivy report is unavailable or invalid: {path.name}") from exc
    results = report.get("Results")
    if results is None and all(
        key in report for key in ("SchemaVersion", "ArtifactName", "ArtifactType")
    ):
        results = []
    if not isinstance(results, list):
        raise ValueError(f"Trivy report has no Results array: {path.name}")

    findings: list[dict[str, str]] = []
    counts: Counter[str] = Counter()
    for result in results:
        if not isinstance(result, dict):
            continue
        for item in result.get("Vulnerabilities") or []:
            if not isinstance(item, dict):
                continue
            severity = str(item.get("Severity") or "UNKNOWN").upper()
            counts[severity] += 1
            findings.append(
                {
                    "target": str(result.get("Target") or ""),
                    "vulnerability_id": str(item.get("VulnerabilityID") or ""),
                    "package": str(item.get("PkgName") or ""),
                    "installed_version": str(item.get("InstalledVersion") or ""),
                    "fixed_version": str(item.get("FixedVersion") or ""),
                    "severity": severity,
                }
            )
    findings.sort(key=lambda row: (row["severity"], row["vulnerability_id"], row["package"]))
    return {
        "status": "read",
        "report": path.name,
        "result_count": len(results),
        "finding_count": len(findings),
        "severity_counts": dict(sorted(counts.items())),
        "high_critical_count": counts["HIGH"] + counts["CRITICAL"],
        "high_critical_findings": [
            item for item in findings if item["severity"] in {"HIGH", "CRITICAL"}
        ][:100],
    }


def summarize_sbom(path: Path) -> dict[str, Any]:
    try:
        sbom = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"CycloneDX SBOM is unavailable or invalid: {path.name}") from exc
    components = sbom.get("components")
    if not isinstance(components, list):
        raise ValueError(f"CycloneDX SBOM has no components array: {path.name}")
    return {"status": "read", "report": path.name, "component_count": len(components)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", action="append", default=[], metavar="NAME=PATH")
    parser.add_argument("--sbom", type=Path, required=True)
    args = parser.parse_args()

    reports: dict[str, Any] = {}
    errors: list[str] = []
    for value in args.report:
        name, separator, raw_path = value.partition("=")
        if not separator or not name or not raw_path:
            errors.append("report arguments must use NAME=PATH")
            continue
        try:
            reports[name] = summarize_report(Path(raw_path))
        except ValueError as exc:
            errors.append(str(exc))
    sbom_summary: dict[str, Any] | None = None
    try:
        sbom_summary = summarize_sbom(args.sbom)
    except ValueError as exc:
        errors.append(str(exc))

    summary = {
        "schema_version": "aiat.trivy-evidence-summary.v1",
        "status": "complete" if not errors else "incomplete",
        "reports": reports,
        "sbom": sbom_summary,
        "errors": errors,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
