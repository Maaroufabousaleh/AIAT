"""Static and read-only live worker inventory reconciliation checks."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path


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
    assert all(row["evidence_status"] == "findings_review_required" for row in pending.values())
    assert all(row["finding_count"] == 316 for row in pending.values())


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
