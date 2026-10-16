"""Scanner result inconsistencies fail closed before they become PASS evidence."""

from __future__ import annotations

import json

import pytest
from orchestrator_api import worker_security_scan


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_scan_environment_disables_semgrep_version_check_and_metrics(tmp_path) -> None:
    environment = worker_security_scan._scan_environment(tmp_path / "home", tmp_path / "tmp")

    assert environment["SEMGREP_ENABLE_VERSION_CHECK"] == "0"
    assert environment["SEMGREP_SEND_METRICS"] == "off"


@pytest.mark.parametrize(
    ("coverage", "expected_error"),
    [
        (None, "semgrep_scanned_paths_invalid"),
        ([], "semgrep_source_not_scanned"),
    ],
)
def test_semgrep_clean_result_without_source_coverage_is_rejected(
    coverage,
    expected_error: str,
) -> None:
    report = {"results": [], "errors": []}
    if coverage is not None:
        report["paths"] = {"scanned": coverage}

    findings, finding_count, errors = worker_security_scan._semgrep_findings(
        json.dumps(report).encode()
    )

    assert findings == []
    assert finding_count == 0
    assert errors == [expected_error]


def test_semgrep_clean_result_requires_and_accepts_first_party_source_coverage() -> None:
    report = {
        "results": [],
        "errors": [],
        "paths": {
            "scanned": [
                "/tmp/aiat-worker-security-scan-123/source/"
                "aiat_deterministic_file_worker_process.py"
            ]
        },
    }

    findings, finding_count, errors = worker_security_scan._semgrep_findings(
        json.dumps(report).encode()
    )

    assert findings == []
    assert finding_count == 0
    assert errors == []


@pytest.mark.anyio
async def test_trufflehog_findings_exit_without_parsed_findings_is_blocked(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    results = iter(
        [
            {
                "stdout": b"trufflehog 3.97.0\n",
                "stderr": b"",
                "exit_code": 0,
            },
            {"stdout": b"", "stderr": b"", "exit_code": 183},
        ]
    )

    async def execute_bounded(*_args, **_kwargs):
        return next(results)

    monkeypatch.setattr(worker_security_scan, "_execute_bounded", execute_bounded)

    report = await worker_security_scan._one_scanner(
        name="trufflehog",
        argv=["/usr/local/bin/trufflehog", "filesystem", "--json", str(tmp_path)],
        source_dir=tmp_path,
        home=tmp_path / "home",
        temp=tmp_path / "tmp",
    )

    assert report["exit_code"] == 183
    assert report["finding_count"] == 0
    assert report["status"] == "blocked"
    assert report["scanner_errors"] == ["trufflehog_findings_exit_without_findings"]
