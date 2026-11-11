from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scan_first_party_worker_source.py"


def _module():
    spec = importlib.util.spec_from_file_location("scan_first_party_worker_source", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_safe_report_excludes_raw_scanner_material():
    module = _module()
    report = {
        "schema_version": "scan.v1",
        "status": "findings_review_required",
        "finding_count": 1,
        "raw_stdout": "token-value-must-not-escape",
        "scanners": [
            {
                "name": "trufflehog",
                "status": "pass",
                "stdout_bytes": 10,
                "stdout": "token-value-must-not-escape",
                "findings": [
                    {
                        "rule_id": "detector",
                        "path": "worker.py",
                        "line": 7,
                        "severity": "SECRET_DETECTION",
                        "raw": "token-value-must-not-escape",
                    }
                ],
            }
        ],
    }

    safe = module.safe_report(report)
    encoded = __import__("json").dumps(safe)

    assert "token-value-must-not-escape" not in encoded
    assert "raw_stdout" not in encoded
    assert safe["scanners"][0]["findings"] == [
        {
            "rule_id": "detector",
            "path": "worker.py",
            "line": 7,
            "severity": "SECRET_DETECTION",
        }
    ]


def test_candidate_identity_is_explicitly_unregistered():
    module = _module()
    worker, provenance = module._candidate_identity()

    assert worker["id"] == "ci-unregistered-candidate"
    assert worker["tool_grants"] == ["file_write"]
    assert worker["permission_grants"] == ["project.workspace.write"]
    assert provenance["oci_image_digest"].startswith("sha256:")
