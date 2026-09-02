"""Focused tests for the bounded object-store process-outage checker."""

from __future__ import annotations

import json

from check_object_store_provider_outage import main


def test_fixture_checks_both_provider_shapes_and_cleanup(capsys) -> None:
    assert main(["--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["schema_version"] == "aiat.object-store-provider-outage.v1"
    assert report["status"] == "pass"
    assert [provider["provider"] for provider in report["providers"]] == ["minio", "seaweedfs"]
    assert all(provider["cleanup_verified"] for provider in report["providers"])
    assert all(provider["remaining_fixture_count"] == 0 for provider in report["providers"])
    assert all(provider["process_outage_observed"] == "not_checked" for provider in report["providers"])
    assert "fixture payload" not in json.dumps(report, sort_keys=True)


def test_live_without_reserved_provider_configuration_is_blocked(capsys) -> None:
    assert main(["--live", "--json"]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "blocked"
    assert report["providers"] == []
    assert report["failure_classification"]["harness_or_configuration_failure"] == (
        "blocked before provider execution"
    )
    assert "minio.secret_key" in report["missing_configuration"]
    assert "seaweedfs.secret_key" in report["missing_configuration"]
