"""Focused tests for retained live-evidence release-ledger consumption."""

from __future__ import annotations

import json

from check_release_ledger import (
    CheckSpec,
    _load_inventory,
    _run_retained_live_evidence,
    _validate_retained_live_evidence,
)


def _spec(path: str, schema: str, check_id: str) -> CheckSpec:
    return CheckSpec(
        check_id=check_id,
        category="operations",
        script="scripts/check_object_store_resource_profile.py",
        args=("--json",),
        live_args=("--live", "--json"),
        retained_evidence_path=path,
        retained_evidence_schema=schema,
    )


def test_inventory_registers_retained_object_store_live_evidence() -> None:
    _, checks = _load_inventory()
    retained = {
        check.check_id: (check.retained_evidence_path, check.retained_evidence_schema)
        for check in checks
        if check.retained_evidence_path
    }
    assert set(retained) == {
        "object_store_multipart",
        "object_store_resource_profile",
        "object_store_provider_outage",
    }
    assert retained["object_store_resource_profile"][1] == "aiat.object-store-resource-profile.v1"


def test_retained_provider_evidence_is_scalar_and_passes() -> None:
    spec = _spec(
        "docs/provenance/object_store_resource_profile_provider_diverse_evidence.json",
        "aiat.object-store-resource-profile.v1",
        "object_store_resource_profile",
    )
    status, reason, payload = _validate_retained_live_evidence(spec)
    assert status == "pass"
    assert reason == "retained live evidence validated"
    assert payload is not None
    row = _run_retained_live_evidence(spec)
    assert row["status"] == "pass"
    assert row["summary"]["evidence_mode"] == "retained-live"
    assert "providers" in row["summary"]


def test_retained_multipart_and_outage_evidence_pass() -> None:
    cases = (
        (
            "docs/provenance/object_store_multipart_provider_diverse_evidence.json",
            "aiat.object-store-multipart.v1",
            "object_store_multipart",
        ),
        (
            "docs/provenance/object_store_provider_outage_live_evidence.json",
            "aiat.object-store-provider-outage.v1",
            "object_store_provider_outage",
        ),
    )
    for path, schema, check_id in cases:
        spec = _spec(path, schema, check_id)
        assert _validate_retained_live_evidence(spec)[:2] == (
            "pass",
            "retained live evidence validated",
        )


def test_retained_evidence_missing_file_is_blocked(monkeypatch, tmp_path) -> None:
    import check_release_ledger

    monkeypatch.setattr(check_release_ledger, "MAS_ROOT", tmp_path)
    spec = _spec("missing.json", "aiat.object-store-resource-profile.v1", "object_store_resource_profile")
    status, reason, payload = _validate_retained_live_evidence(spec)
    assert status == "blocked"
    assert "unavailable" in reason
    assert payload is None


def test_retained_evidence_rejects_provider_failure(monkeypatch, tmp_path) -> None:
    import check_release_ledger

    evidence = {
        "schema_version": "aiat.object-store-resource-profile.v1",
        "evidence_commit": "deadbeef",
        "observed_at": "2026-08-18T00:00:00Z",
        "mode": "live-bounded-resource-profile",
        "status": "pass",
        "providers": [
            {"name": "minio", "status": "fail", "error_count": 1},
            {"name": "seaweedfs", "status": "pass", "error_count": 0},
        ],
        "invalid_run_excluded": True,
        "payload_free": True,
        "secret_free": True,
        "licence_metadata_is_gate": False,
    }
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(evidence), encoding="utf-8")
    monkeypatch.setattr(check_release_ledger, "MAS_ROOT", tmp_path)
    spec = _spec("evidence.json", "aiat.object-store-resource-profile.v1", "object_store_resource_profile")
    status, reason, _ = _validate_retained_live_evidence(spec)
    assert status == "fail"
    assert "minio did not pass" in reason
