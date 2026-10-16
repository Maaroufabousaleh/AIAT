"""Unverified scalar security-scan claims cannot satisfy worker governance."""

from __future__ import annotations

import hashlib
import json
import stat
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest


@pytest.mark.anyio
async def test_external_worker_registration_rejects_scalar_scan_pass(client, monkeypatch) -> None:
    from orchestrator_api import main

    storage = MagicMock()
    storage.register_worker = AsyncMock()
    monkeypatch.setattr(main, "_storage", lambda: storage)

    response = await client.post(
        "/capabilities/workers",
        json={
            "name": "unverified-worker",
            "adapter_type": "oci",
            "source_repo": "https://github.com/example/worker",
            "version_pin": "1.0.0",
            "sandbox_profile": "sandboxed",
            "adapter_config": {
                "provenance": {"security_scan_status": " PaSsEd "},
            },
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "SECURITY_SCAN_EVIDENCE_REQUIRED"
    storage.register_worker.assert_not_awaited()


@pytest.mark.anyio
async def test_steward_creation_rejects_scalar_scan_pass_before_persisting(client, monkeypatch) -> None:
    from orchestrator_api import main

    worker_id = uuid4()
    storage = MagicMock()
    storage.get_worker = AsyncMock(
        return_value={"id": worker_id, "source_repo": "https://github.com/example/worker"}
    )
    storage.get_steward_by_worker = AsyncMock(return_value=None)
    storage.create_external_provenance = AsyncMock()
    storage.create_steward = AsyncMock()
    monkeypatch.setattr(main, "_storage", lambda: storage)

    response = await client.post(
        f"/capabilities/workers/{worker_id}/steward",
        headers={"X-API-Key": "test-operator-key"},
        json={
            "source_repo": "https://github.com/example/worker",
            "exact_release": "1.0.0",
            "security_scan_status": "passed",
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "SECURITY_SCAN_EVIDENCE_REQUIRED"
    storage.create_external_provenance.assert_not_awaited()
    storage.create_steward.assert_not_awaited()


@pytest.mark.anyio
async def test_manifest_import_rejects_scalar_scan_pass_without_writes(
    client, monkeypatch, tmp_path
) -> None:
    from orchestrator_api import main

    workers_dir = tmp_path / "workers"
    workers_dir.mkdir()
    (workers_dir / "unverified-worker.yaml").write_text(
        """
metadata:
  id: unverified-worker
  name: Unverified Worker
  version: "1.0.0"
  source_repo: https://github.com/example/unverified-worker
  version_pin: "1.0.0"
runtime:
  transport: process
integration:
  isolation_mode: wrapper
sandbox:
  profile: sandboxed
runtime_tier: external
source_provenance:
  security_scan_status: passed
""".strip()
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    storage = MagicMock()
    storage.get_worker_by_name = AsyncMock()
    storage.register_worker = AsyncMock()
    storage.create_capability = AsyncMock()
    monkeypatch.setattr(main, "_storage", lambda: storage)

    response = await client.post(
        "/capabilities/workers/import",
        headers={"X-API-Key": "test-operator-key"},
        json={"workers_dir": str(workers_dir)},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["errors"] == 1
    assert payload["details"][0]["details"].startswith(
        "SECURITY_SCAN_EVIDENCE_REQUIRED:"
    )
    storage.get_worker_by_name.assert_not_awaited()
    storage.register_worker.assert_not_awaited()
    storage.create_capability.assert_not_awaited()


@pytest.mark.anyio
async def test_candidate_certification_rejects_legacy_scalar_scan_pass(client, monkeypatch) -> None:
    from orchestrator_api import main

    from mas_core.worker_registry.source_bound_security_scan import (
        FIRST_PARTY_IMAGE_DIGEST,
        FIRST_PARTY_PERMISSION_GRANTS,
        FIRST_PARTY_RUNTIME_ADAPTER_CONFIG,
        FIRST_PARTY_RUNTIME_SHA256,
        FIRST_PARTY_SOURCE_REPOSITORY,
        FIRST_PARTY_SOURCE_REVISION,
        FIRST_PARTY_TOOL_GRANTS,
        FIRST_PARTY_WORKER_ID,
        FIRST_PARTY_WORKER_VERSION,
    )
    from mas_core.worker_registry.steward import (
        CandidateIntakeStatus,
        ExternalProvenance,
        ExternalWorkerSteward,
        StewardStatus,
    )

    worker_id = uuid4()
    steward = ExternalWorkerSteward(
        worker_id=str(worker_id),
        steward_id=uuid4(),
        provenance=ExternalProvenance(
            canonical_source_repository=FIRST_PARTY_SOURCE_REPOSITORY,
            source_provider="aiat",
            exact_release=FIRST_PARTY_WORKER_VERSION,
            commit_sha=FIRST_PARTY_SOURCE_REVISION,
            oci_image_digest=FIRST_PARTY_IMAGE_DIGEST,
            runtime_fingerprint=f"sha256:{FIRST_PARTY_RUNTIME_SHA256}",
            transport_type="oci",
            # Simulates a legacy persisted row written before ingress closed.
            security_scan_status="passed",
        ),
        status=StewardStatus.READY,
    )
    candidate = steward.generate_candidate(
        semantic_version="1.0.0",
        adapter_version="1.0.0",
        upstream_compatibility_range="==1.0.0",
    )
    for stage in (
        CandidateIntakeStatus.SOURCE_REVIEW,
        CandidateIntakeStatus.SECURITY_REVIEW,
        CandidateIntakeStatus.INTERFACE_RESEARCH,
        CandidateIntakeStatus.GENERATED,
        CandidateIntakeStatus.CERTIFYING,
    ):
        steward.advance_candidate(candidate.candidate_id, stage)

    storage = MagicMock()
    storage.get_worker = AsyncMock(
        return_value={
            "id": worker_id,
            "name": FIRST_PARTY_WORKER_ID,
            "version": FIRST_PARTY_WORKER_VERSION,
            "version_pin": f"{FIRST_PARTY_WORKER_VERSION}+{FIRST_PARTY_IMAGE_DIGEST.replace(':', '.')}",
            "source_repo": FIRST_PARTY_SOURCE_REPOSITORY,
            "source_revision": FIRST_PARTY_SOURCE_REVISION,
            "adapter_type": "oci",
            "adapter_entrypoint": "ProcessAdapter",
            "sandbox_profile": "sandboxed",
            "isolation_mode": "wrapper",
            "wrapper_config": {},
            "model_mode": "none",
            "model_profile_id": None,
            "tool_grants": list(FIRST_PARTY_TOOL_GRANTS),
            "permission_grants": list(FIRST_PARTY_PERMISSION_GRANTS),
            "adapter_config": dict(FIRST_PARTY_RUNTIME_ADAPTER_CONFIG),
        }
    )
    storage.get_skill_bundle_candidate = AsyncMock(return_value=None)
    storage.get_external_provenance_by_worker = AsyncMock(
        return_value={
            "security_scan_status": "passed",
            "canonical_source_repository": FIRST_PARTY_SOURCE_REPOSITORY,
            "source_provider": "aiat",
            "exact_release": FIRST_PARTY_WORKER_VERSION,
            "commit_sha": FIRST_PARTY_SOURCE_REVISION,
            "oci_image_digest": FIRST_PARTY_IMAGE_DIGEST,
            "runtime_fingerprint": f"sha256:{FIRST_PARTY_RUNTIME_SHA256}",
            "transport_type": "oci",
        }
    )
    storage.get_steward_by_worker = AsyncMock(
        return_value={"id": uuid4(), "metadata": {"source_provenance_extra": {}}}
    )
    storage.create_certification_run = AsyncMock()
    storage.create_compatibility_matrix = AsyncMock()
    monkeypatch.setattr(main, "_storage", lambda: storage)
    monkeypatch.setattr(main, "_steward_runtime", AsyncMock(return_value=steward))

    response = await client.post(
        f"/capabilities/workers/{worker_id}/steward/candidates/{candidate.candidate_id}/certify",
        headers={"X-API-Key": "test-operator-key"},
        json={},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "SECURITY_SCAN_EVIDENCE_REQUIRED"
    assert storage.get_worker.await_args_list == [
        ((worker_id,), {}),
        ((worker_id,), {}),
    ]
    storage.create_certification_run.assert_not_awaited()
    storage.create_compatibility_matrix.assert_not_awaited()


@pytest.mark.anyio
async def test_worker_activation_does_not_trust_legacy_scalar_scan_pass() -> None:
    from orchestrator_api import main

    from mas_core.worker_registry.source_bound_security_scan import (
        FIRST_PARTY_IMAGE_DIGEST,
        FIRST_PARTY_RUNTIME_SHA256,
        FIRST_PARTY_SOURCE_REPOSITORY,
        FIRST_PARTY_SOURCE_REVISION,
        FIRST_PARTY_WORKER_ID,
        FIRST_PARTY_WORKER_VERSION,
    )

    worker_id = uuid4()
    storage = MagicMock()
    storage.get_worker_shell_version = AsyncMock(return_value={"status": "active"})
    storage.get_runtime_adapter = AsyncMock(
        return_value={"status": "active", "conformance_status": "passed"}
    )
    storage.get_skill_bundle = AsyncMock(return_value={"status": "APPROVED"})
    storage.list_capability_snapshots = AsyncMock(return_value=[{"id": uuid4()}])
    storage.get_steward_by_worker = AsyncMock(return_value={"id": uuid4()})
    storage.get_external_provenance_by_worker = AsyncMock(
        return_value={
            "security_scan_status": "passed",
            "canonical_source_repository": FIRST_PARTY_SOURCE_REPOSITORY,
            "source_provider": "aiat",
            "exact_release": FIRST_PARTY_WORKER_VERSION,
            "commit_sha": FIRST_PARTY_SOURCE_REVISION,
            "oci_image_digest": FIRST_PARTY_IMAGE_DIGEST,
            "runtime_fingerprint": f"sha256:{FIRST_PARTY_RUNTIME_SHA256}",
            "transport_type": "oci",
        }
    )
    worker = {
        "id": worker_id,
        "name": FIRST_PARTY_WORKER_ID,
        "version": FIRST_PARTY_WORKER_VERSION,
        "source_repo": FIRST_PARTY_SOURCE_REPOSITORY,
        "source_revision": FIRST_PARTY_SOURCE_REVISION,
        "version_pin": f"{FIRST_PARTY_WORKER_VERSION}+{FIRST_PARTY_IMAGE_DIGEST.replace(':', '.')}",
        "adapter_type": "oci",
        "adapter_entrypoint": "ProcessAdapter",
        "sandbox_profile": "sandboxed",
        "adapter_config": {
            "image": f"aiat-deterministic-file-worker@{FIRST_PARTY_IMAGE_DIGEST}"
        },
        "active_shell_version_id": uuid4(),
        "active_adapter_id": uuid4(),
        "active_skill_bundle_id": uuid4(),
        "model_mode": "none",
    }

    blockers = await main._worker_activation_blockers(storage, worker)

    assert "external worker security scan is missing required governed evidence" in blockers


@pytest.mark.anyio
async def test_generic_external_worker_keeps_existing_scalar_status_promotion_path() -> None:
    from orchestrator_api import main

    worker_id = uuid4()
    storage = MagicMock()
    storage.get_worker_shell_version = AsyncMock(return_value={"status": "active"})
    storage.get_runtime_adapter = AsyncMock(
        return_value={"status": "active", "conformance_status": "passed"}
    )
    storage.get_skill_bundle = AsyncMock(return_value={"status": "APPROVED"})
    storage.list_capability_snapshots = AsyncMock(return_value=[{"id": uuid4()}])
    storage.get_steward_by_worker = AsyncMock(return_value={"id": uuid4()})
    storage.get_external_provenance_by_worker = AsyncMock(
        return_value={
            "security_scan_status": "passed",
            "canonical_source_repository": "https://github.com/example/worker",
            "source_provider": "github",
            "exact_release": "1.0.0",
            "transport_type": "oci",
        }
    )
    worker = {
        "id": worker_id,
        "name": "generic_external_worker",
        "source_repo": "https://github.com/example/worker",
        "version_pin": "1.0.0",
        "adapter_config": {},
        "active_shell_version_id": uuid4(),
        "active_adapter_id": uuid4(),
        "active_skill_bundle_id": uuid4(),
        "model_mode": "none",
    }

    blockers = await main._worker_activation_blockers(storage, worker)

    assert not any("security scan" in blocker for blocker in blockers)


def test_source_scan_rejects_manifest_permission_change_with_identity_unchanged() -> None:
    from orchestrator_api.worker_security_scan import (
        WorkerSecurityScanError,
        _validate_manifest_and_registered_identity,
    )

    from mas_core.worker_registry.source_bound_security_scan import (
        FIRST_PARTY_IMAGE_DIGEST,
        FIRST_PARTY_RUNTIME_SHA256,
        FIRST_PARTY_SOURCE_REPOSITORY,
        FIRST_PARTY_SOURCE_REVISION,
        FIRST_PARTY_WORKER_ID,
        FIRST_PARTY_WORKER_VERSION,
    )

    root = Path(__file__).resolve().parents[3]
    manifest = (root / "workers" / f"{FIRST_PARTY_WORKER_ID}.yaml").read_bytes()
    source = (root / "workers" / f"{FIRST_PARTY_WORKER_ID}_process.py").read_bytes()
    dockerfile = (root / "workers" / f"Dockerfile.{FIRST_PARTY_WORKER_ID}").read_bytes()
    config = (root / "apps/tool-service/tool_service/semgrep-default.yml").read_bytes()
    worker_id = str(uuid4())
    worker = {
        "id": worker_id,
        "name": FIRST_PARTY_WORKER_ID,
        "version": FIRST_PARTY_WORKER_VERSION,
        "version_pin": f"{FIRST_PARTY_WORKER_VERSION}+{FIRST_PARTY_IMAGE_DIGEST.replace(':', '.')}",
        "source_repo": FIRST_PARTY_SOURCE_REPOSITORY,
        "source_revision": FIRST_PARTY_SOURCE_REVISION,
        "adapter_type": "oci",
        "adapter_entrypoint": "ProcessAdapter",
        "sandbox_profile": "sandboxed",
        "adapter_config": {
            "image": f"aiat-deterministic-file-worker@{FIRST_PARTY_IMAGE_DIGEST}"
        },
    }
    provenance = {
        "canonical_source_repository": FIRST_PARTY_SOURCE_REPOSITORY,
        "source_provider": "aiat",
        "exact_release": FIRST_PARTY_WORKER_VERSION,
        "commit_sha": FIRST_PARTY_SOURCE_REVISION,
        "oci_image_digest": FIRST_PARTY_IMAGE_DIGEST,
        "transport_type": "oci",
        "runtime_fingerprint": f"sha256:{FIRST_PARTY_RUNTIME_SHA256}",
    }
    changed_permission = manifest.replace(
        b"permission_grants: [project.workspace.write]",
        b"permission_grants: [project.workspace.read]",
    )
    assert changed_permission != manifest

    with pytest.raises(WorkerSecurityScanError, match="candidate_source_identity_mismatch"):
        _validate_manifest_and_registered_identity(
            manifest_bytes=changed_permission,
            source_bytes=source,
            dockerfile_bytes=dockerfile,
            config_bytes=config,
            worker=worker,
            provenance=provenance,
        )


@pytest.mark.anyio
async def test_server_scan_uses_read_only_hashed_semgrep_config_snapshot(monkeypatch) -> None:
    from orchestrator_api import worker_security_scan

    from mas_core.worker_registry.source_bound_security_scan import (
        FIRST_PARTY_BUILD_PROVENANCE_SUMMARY,
        FIRST_PARTY_IMAGE_DIGEST,
        FIRST_PARTY_MANIFEST_SHA256,
        FIRST_PARTY_PERMISSION_GRANTS,
        FIRST_PARTY_RUNTIME_ADAPTER_CONFIG,
        FIRST_PARTY_RUNTIME_SHA256,
        FIRST_PARTY_SEMGREP_CONFIG_SHA256,
        FIRST_PARTY_SOURCE_REPOSITORY,
        FIRST_PARTY_SOURCE_REVISION,
        FIRST_PARTY_TOOL_GRANTS,
        FIRST_PARTY_WORKER_ID,
        FIRST_PARTY_WORKER_VERSION,
        source_bound_security_scan_report_integrity_is_valid,
    )

    root = Path(__file__).resolve().parents[3]
    monkeypatch.chdir(root)
    worker_id = str(uuid4())
    worker = {
        "id": worker_id,
        "name": FIRST_PARTY_WORKER_ID,
        "version": FIRST_PARTY_WORKER_VERSION,
        "version_pin": f"{FIRST_PARTY_WORKER_VERSION}+{FIRST_PARTY_IMAGE_DIGEST.replace(':', '.')}",
        "source_repo": FIRST_PARTY_SOURCE_REPOSITORY,
        "source_revision": FIRST_PARTY_SOURCE_REVISION,
        "adapter_type": "oci",
        "adapter_entrypoint": "ProcessAdapter",
        "sandbox_profile": "sandboxed",
        "isolation_mode": "wrapper",
        "wrapper_config": {},
        "model_mode": "none",
        "model_profile_id": None,
        "tool_grants": list(FIRST_PARTY_TOOL_GRANTS),
        "permission_grants": list(FIRST_PARTY_PERMISSION_GRANTS),
        "adapter_config": {
            **FIRST_PARTY_RUNTIME_ADAPTER_CONFIG,
            "legacy_external_wrapper": False,
        },
    }
    provenance = {
        "canonical_source_repository": FIRST_PARTY_SOURCE_REPOSITORY,
        "source_provider": "aiat",
        "exact_release": FIRST_PARTY_WORKER_VERSION,
        "commit_sha": FIRST_PARTY_SOURCE_REVISION,
        "oci_image_digest": FIRST_PARTY_IMAGE_DIGEST,
        "transport_type": "oci",
        "runtime_fingerprint": f"sha256:{FIRST_PARTY_RUNTIME_SHA256}",
    }
    observed_snapshot: dict[str, object] = {}

    async def fake_execute(argv, *, cwd, env, stdout_limit, stderr_limit):
        del stdout_limit, stderr_limit
        assert not any(key.lower() in {"http_proxy", "https_proxy", "all_proxy"} for key in env)
        if argv[1] == "--version":
            version = (
                "1.168.0"
                if argv[0] == worker_security_scan.PINNED_SCANNERS["semgrep"]["executable"]
                else "trufflehog 3.97.0"
            )
            return {
                "exit_code": 0,
                "stdout": version.encode() + b"\n",
                "stderr": b"",
                "error": None,
            }
        if argv[1] == "scan":
            config_path = Path(argv[argv.index("--config") + 1])
            config_bytes = config_path.read_bytes()
            observed_snapshot.update(
                path=str(config_path),
                sha256=hashlib.sha256(config_bytes).hexdigest(),
                mode=stat.S_IMODE(config_path.stat().st_mode),
                source_dir=str(cwd),
                source_dir_mode=stat.S_IMODE(cwd.stat().st_mode),
                private_base_mode=stat.S_IMODE(cwd.parent.stat().st_mode),
            )
            return {
                "exit_code": 0,
                "stdout": json.dumps(
                    {
                        "results": [],
                        "errors": [],
                        "paths": {
                            "scanned": [
                                str(cwd / "aiat_deterministic_file_worker_process.py")
                            ]
                        },
                    }
                ).encode(),
                "stderr": b"",
                "error": None,
            }
        return {"exit_code": 0, "stdout": b"", "stderr": b"", "error": None}

    monkeypatch.setattr(worker_security_scan, "_execute_bounded", fake_execute)
    report = await worker_security_scan.run_first_party_worker_source_scan(
        worker=worker,
        provenance=provenance,
    )

    assert report["status"] == "passed"
    assert report["manifest_sha256"] == FIRST_PARTY_MANIFEST_SHA256
    assert report["image_build_provenance"] == FIRST_PARTY_BUILD_PROVENANCE_SUMMARY
    assert report["semgrep_config_sha256"] == FIRST_PARTY_SEMGREP_CONFIG_SHA256
    assert observed_snapshot["path"] == report["semgrep_config_path"]
    assert observed_snapshot["sha256"] == FIRST_PARTY_SEMGREP_CONFIG_SHA256
    assert observed_snapshot["mode"] == 0o444
    assert observed_snapshot["source_dir_mode"] == 0o500
    assert observed_snapshot["private_base_mode"] == 0o700
    assert report["semgrep_config_snapshot_mode"] == "0444"
    assert report["scanner_network_isolation"] == "not_enforced_by_scanner_process"
    assert str(report["scanner_source_dir"]).startswith("/tmp/aiat-worker-security-scan-")
    assert report["semgrep_config_path"] == (
        f"{Path(str(report['scanner_source_dir'])).parent}/semgrep-default.yml"
    )
    assert report["scanners"][0]["argv"][3] == report["semgrep_config_path"]
    assert source_bound_security_scan_report_integrity_is_valid(
        report,
        expected_worker_id=worker_id,
        provenance={**provenance, "security_scan_status": "passed"},
        current_worker=worker,
    )


def test_image_build_provenance_requires_the_exact_retained_record(tmp_path: Path) -> None:
    from orchestrator_api.worker_security_scan import (
        WorkerSecurityScanError,
        _load_first_party_image_build_provenance,
    )

    repository_root = Path(__file__).resolve().parents[3]
    source_record = (
        repository_root / "docs/provenance/deterministic_file_worker_build_provenance.json"
    )
    target_record = (
        tmp_path / "docs/provenance/deterministic_file_worker_build_provenance.json"
    )
    target_record.parent.mkdir(parents=True)
    target_record.write_bytes(source_record.read_bytes())

    assert _load_first_party_image_build_provenance(tmp_path)["status"] == (
        "local_build_binding_observed"
    )

    target_record.write_bytes(target_record.read_bytes() + b" ")
    with pytest.raises(WorkerSecurityScanError, match="candidate_build_provenance_mismatch"):
        _load_first_party_image_build_provenance(tmp_path)


@pytest.mark.anyio
async def test_source_scan_is_operator_only_and_persists_only_server_generated_report(
    client,
    monkeypatch,
) -> None:
    from orchestrator_api import main, worker_security_scan

    worker_id = uuid4()
    worker = {"id": worker_id, "name": "aiat_deterministic_file_worker"}
    provenance = {"provenance_hash": "p" * 64, "security_scan_status": "pending"}
    steward = {"id": uuid4(), "metadata": {}}
    server_report = {"schema_version": "test-server-scan", "status": "blocked"}

    storage = MagicMock()
    storage.get_worker = AsyncMock(return_value=worker)
    storage.get_external_provenance_by_worker = AsyncMock(return_value=provenance)
    storage.get_steward_by_worker = AsyncMock(return_value=steward)
    storage.persist_source_bound_worker_security_scan = AsyncMock(
        return_value={"security_scan_status": "blocked"}
    )
    scanner = AsyncMock(return_value=server_report)
    monkeypatch.setattr(worker_security_scan, "run_first_party_worker_source_scan", scanner)
    monkeypatch.setattr(main, "_storage", lambda: storage)

    unauthorized = await client.post(
        f"/capabilities/workers/{worker_id}/steward/security-scan",
        headers={"X-API-Key": "test-mas-key"},
        json={"scan": {"status": "passed"}},
    )
    assert unauthorized.status_code == 403
    scanner.assert_not_awaited()
    storage.persist_source_bound_worker_security_scan.assert_not_awaited()

    response = await client.post(
        f"/capabilities/workers/{worker_id}/steward/security-scan",
        headers={"X-API-Key": "test-operator-key"},
        json={"scan": {"status": "passed", "findings": []}},
    )
    assert response.status_code == 200
    assert response.json()["scan"] == server_report
    scanner.assert_awaited_once_with(worker=worker, provenance=provenance)
    storage.persist_source_bound_worker_security_scan.assert_awaited_once_with(
        worker_id=worker_id,
        report=server_report,
        expected_provenance_hash="p" * 64,
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("error_code", "expected_status"),
    [
        ("SECURITY_SCAN_PROVENANCE_CHANGED", 409),
        ("SECURITY_SCAN_REPORT_INVALID", 422),
        ("SECURITY_SCAN_REPORT_TOO_LARGE", 422),
    ],
)
async def test_source_scan_persistence_errors_preserve_stable_failure_category(
    client,
    monkeypatch,
    error_code: str,
    expected_status: int,
) -> None:
    from orchestrator_api import main, worker_security_scan

    from mas_core.worker_registry.source_bound_security_scan import (
        SourceBoundSecurityScanPersistenceError,
    )

    worker_id = uuid4()
    worker = {"id": worker_id, "name": "aiat_deterministic_file_worker"}
    provenance = {"provenance_hash": "p" * 64, "security_scan_status": "pending"}
    storage = MagicMock()
    storage.get_worker = AsyncMock(return_value=worker)
    storage.get_external_provenance_by_worker = AsyncMock(return_value=provenance)
    storage.get_steward_by_worker = AsyncMock(return_value={"id": uuid4(), "metadata": {}})
    storage.persist_source_bound_worker_security_scan = AsyncMock(
        side_effect=SourceBoundSecurityScanPersistenceError(error_code)
    )
    monkeypatch.setattr(
        worker_security_scan,
        "run_first_party_worker_source_scan",
        AsyncMock(return_value={"status": "blocked"}),
    )
    monkeypatch.setattr(main, "_storage", lambda: storage)

    response = await client.post(
        f"/capabilities/workers/{worker_id}/steward/security-scan",
        headers={"X-API-Key": "test-operator-key"},
    )

    assert response.status_code == expected_status
    assert response.json()["detail"]["code"] == error_code
