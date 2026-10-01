"""Static and read-only live worker inventory reconciliation checks."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import yaml


def test_checked_in_worker_inventory_reconciles() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    result = subprocess.run(
        [sys.executable, "scripts/check_worker_reconciliation.py", "--json"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    report = json.loads(result.stdout)

    assert report["status"] == "pass"
    assert report["worker_count"] >= 39
    assert set(report["runtime_counts"]) >= {"builtin", "crewai", "external", "langgraph"}
    assert set(report["transport_counts"]) >= {"human", "opencode", "process"}
    pending = {row["worker"]: row for row in report["pending_evidence"]}
    assert set(pending) == {
        "coding_worker",
        "tester",
    }
    assert all(row["evidence_status"] == "historical_unreproducible" for row in pending.values())
    assert all(row["finding_count_status"] == "unavailable_no_retained_raw_evidence" for row in pending.values())
    assert all("finding_count" not in row for row in pending.values())


def _load_checker():
    repo_root = Path(__file__).resolve().parents[3]
    path = repo_root / "scripts" / "check_worker_reconciliation.py"
    spec = importlib.util.spec_from_file_location("worker_reconciliation_checker", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, repo_root


def test_live_worker_binding_reconciliation_is_read_only_and_complete(monkeypatch) -> None:
    module, repo_root = _load_checker()
    rows = []
    for path in sorted((repo_root / "workers").glob("*.yaml")):
        manifest = module.WorkerManifest.model_validate(module._load_yaml(path))
        rows.append(
            {
                "name": manifest.metadata.id,
                "adapter_type": manifest.runtime.transport,
                "adapter_entrypoint": manifest.integration.adapter_entrypoint,
                "isolation_mode": manifest.integration.isolation_mode,
                "sandbox_profile": manifest.sandbox.profile,
                "team_id": module._manifest_team_id(manifest),
                "source_repo": (
                    None
                    if manifest.metadata.source_repo == "local"
                    else manifest.metadata.source_repo
                ),
                "source_revision": manifest.metadata.source_revision,
                "version_pin": manifest.metadata.version_pin,
                "model_mode": manifest.model_mode,
                "model_profile_id": manifest.model_profile_id,
                "capability_names": [cap.name for cap in manifest.capabilities],
                "status": "INACTIVE",
            }
        )

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return rows

    monkeypatch.setattr(module.httpx, "get", lambda *args, **kwargs: Response())
    report = module._live_reconcile(
        workers_dir=repo_root / "workers",
        url="http://orchestrator.invalid",
        api_key="secret-value",
        timeout=1,
    )
    assert report["status"] == "pass"
    assert report["matched_count"] == report["default_worker_count"] >= 39
    assert report["licence_metadata_is_gate"] is False
    assert "secret-value" not in json.dumps(report)


def test_live_worker_binding_reconciliation_requires_immutable_active_records(monkeypatch) -> None:
    module, repo_root = _load_checker()
    manifest = module.WorkerManifest.model_validate(
        module._load_yaml(repo_root / "workers" / "coding_worker.yaml")
    )
    row = {
        "name": manifest.metadata.id,
        "adapter_type": manifest.runtime.transport,
        "adapter_entrypoint": manifest.integration.adapter_entrypoint,
        "isolation_mode": manifest.integration.isolation_mode,
        "sandbox_profile": manifest.sandbox.profile,
        "team_id": module._manifest_team_id(manifest),
        "source_repo": manifest.metadata.source_repo,
        "source_revision": manifest.metadata.source_revision,
        "version_pin": manifest.metadata.version_pin,
        "model_mode": manifest.model_mode,
        "model_profile_id": manifest.model_profile_id,
        "capability_names": [cap.name for cap in manifest.capabilities],
        "status": "ACTIVE",
    }

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return [row]

    monkeypatch.setattr(module.httpx, "get", lambda *args, **kwargs: Response())
    report = module._live_reconcile(
        workers_dir=repo_root / "workers",
        url="http://orchestrator.invalid",
        api_key="secret-value",
        timeout=1,
    )
    assert report["status"] == "fail"
    assert report["mismatch_count"] == 1
    assert any("active_adapter_id" in error for error in report["errors"])


def test_live_binding_reconciliation_canonicalizes_legacy_sandbox_alias(monkeypatch) -> None:
    module, repo_root = _load_checker()
    manifest = module.WorkerManifest.model_validate(
        {
            "metadata": {
                "id": "legacy-worker",
                "name": "Legacy worker",
                "source_repo": "https://example.invalid/worker",
            },
            "runtime_tier": "external",
            "sandbox": {"profile": "firecracker"},
        }
    )
    row = {
        "name": manifest.metadata.id,
        "adapter_type": manifest.runtime.transport,
        "adapter_entrypoint": manifest.integration.adapter_entrypoint,
        "isolation_mode": manifest.integration.isolation_mode,
        "sandbox_profile": "vm_isolated",
        "team_id": module._manifest_team_id(manifest),
        "source_repo": manifest.metadata.source_repo,
        "source_revision": manifest.metadata.source_revision,
        "version_pin": manifest.metadata.version_pin,
        "model_mode": manifest.model_mode,
        "model_profile_id": manifest.model_profile_id,
        "capability_names": [],
        "status": "INACTIVE",
    }

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return [row]

    monkeypatch.setattr(module.httpx, "get", lambda *args, **kwargs: Response())
    report = module._live_reconcile(
        workers_dir=repo_root / "workers",
        url="http://orchestrator.invalid",
        api_key="secret-value",
        timeout=1,
    )

    # The fixture is intentionally not a complete default inventory; the
    # assertion targets the alias comparison rather than the overall count.
    assert not any("legacy-worker" in error and "sandbox_profile" in error for error in report["errors"])


def test_security_evidence_accepts_scanner_without_file_count_metric(tmp_path) -> None:
    module, _ = _load_checker()
    path = tmp_path / "security-scan-evidence.json"
    path.write_text(
        json.dumps(
            {
                "policy": {
                    "security_scan_is_technical_gate": True,
                    "licence_metadata_is_gate": False,
                },
                "scans": [
                    {
                        "id": "scanner-without-count",
                        "scanner": "example-scanner",
                        "scanner_version": "1.2.3",
                        "source_repo": "https://example.invalid/source",
                        "source_revision": "v1.2.3",
                        "target_workers": ["coding_worker"],
                        "status": "findings_review_required",
                        "scanned_file_count": None,
                        "scanned_file_count_status": "not_reported_by_scanner",
                        "source_snapshot_file_count": 12,
                        "source_snapshot_retained": True,
                        "raw_findings_retained": True,
                        "finding_count": 1,
                        "engine_warning_count": 0,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    errors = []

    indexed = module._security_evidence_index(path, errors)

    assert "scanner-without-count" in indexed
    assert errors == []


def test_unreproducible_historical_scan_cannot_supply_ghost_counts(tmp_path) -> None:
    module, repo_root = _load_checker()
    evidence = module._load_yaml(repo_root / "docs" / "provenance" / "security_scan_evidence.yaml")
    historical = next(row for row in evidence["scans"] if row.get("historical_only") is True)
    historical["finding_count"] = 316
    path = tmp_path / "security-scan-evidence.yaml"
    path.write_text(yaml.safe_dump(evidence, sort_keys=False), encoding="utf-8")
    errors = []

    module._security_evidence_index(path, errors)

    assert any("unreproducible historical evidence must not supply finding_count" in error for error in errors)
