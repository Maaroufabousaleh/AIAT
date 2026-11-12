from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from scripts.summarize_trivy_reports import summarize_report, summarize_sbom

if TYPE_CHECKING:
    from pathlib import Path


def test_summarize_trivy_counts_severity_and_bounds_detail(tmp_path: Path) -> None:
    report_path = tmp_path / "trivy.json"
    report_path.write_text(
        json.dumps(
            {
                "Results": [
                    {
                        "Target": "python:3.11-slim",
                        "Vulnerabilities": [
                            {"VulnerabilityID": "CVE-2026-0001", "PkgName": "openssl", "Severity": "CRITICAL"},
                            {"VulnerabilityID": "CVE-2026-0002", "PkgName": "libc", "Severity": "HIGH"},
                            {"VulnerabilityID": "CVE-2026-0003", "PkgName": "zlib", "Severity": "LOW"},
                        ],
                    },
                    {"Target": "app", "Vulnerabilities": None},
                ]
            }
        ),
        encoding="utf-8",
    )

    summary = summarize_report(report_path)

    assert summary["finding_count"] == 3
    assert summary["severity_counts"] == {"CRITICAL": 1, "HIGH": 1, "LOW": 1}
    assert summary["high_critical_count"] == 2
    assert [item["vulnerability_id"] for item in summary["high_critical_findings"]] == [
        "CVE-2026-0001",
        "CVE-2026-0002",
    ]


def test_summarize_trivy_rejects_missing_or_invalid_results(tmp_path: Path) -> None:
    report_path = tmp_path / "bad.json"
    report_path.write_text('{"SchemaVersion": 2}', encoding="utf-8")

    with pytest.raises(ValueError, match="no Results array"):
        summarize_report(report_path)


def test_summarize_cyclonedx_component_count(tmp_path: Path) -> None:
    sbom_path = tmp_path / "sbom.json"
    sbom_path.write_text(json.dumps({"components": [{"name": "python"}, {"name": "openssl"}]}))

    assert summarize_sbom(sbom_path) == {
        "status": "read",
        "report": "sbom.json",
        "component_count": 2,
    }
