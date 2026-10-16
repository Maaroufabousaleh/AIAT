"""Startup manifest reconciliation must not alter governed lifecycle state."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from mas_core.worker_registry.seeder import seed_workers_from_directory


class _SeedStorage:
    def __init__(self) -> None:
        self.worker_id = uuid4()
        self.register_kwargs: dict[str, object] | None = None

    async def get_worker_by_name(self, name: str):
        assert name == "governed-worker"
        return {
            "id": self.worker_id,
            "name": name,
            "status": "ACTIVE",
            "evaluation_status": "approved",
            "active_shell_version_id": uuid4(),
            "active_adapter_id": uuid4(),
            "active_skill_bundle_id": uuid4(),
        }

    async def register_worker(self, **kwargs):
        self.register_kwargs = kwargs
        return {"id": self.worker_id, "status": kwargs["status"]}


@pytest.mark.anyio
async def test_reseed_preserves_an_existing_governed_worker_lifecycle(tmp_path) -> None:
    (tmp_path / "governed-worker.yaml").write_text(
        """
metadata:
  id: governed-worker
  name: Governed Worker
  version: "1.0.0"
  source_repo: local
runtime:
  transport: process
integration:
  isolation_mode: native
model_mode: none
sandbox:
  profile: standard
""".strip()
        + "\n",
        encoding="utf-8",
    )
    storage = _SeedStorage()

    result = await seed_workers_from_directory(storage, workers_dir=tmp_path)

    assert result[0].action == "updated"
    assert storage.register_kwargs is not None
    assert storage.register_kwargs["status"] == "ACTIVE"
    assert storage.register_kwargs["evaluation_status"] == "approved"


@pytest.mark.anyio
@pytest.mark.parametrize("dry_run", [False, True])
async def test_external_manifest_cannot_assert_scalar_security_scan_pass(tmp_path, dry_run) -> None:
    (tmp_path / "unverified-worker.yaml").write_text(
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
    storage = MagicMock()
    storage.get_worker_by_name = AsyncMock()
    storage.register_worker = AsyncMock()
    storage.create_capability = AsyncMock()

    results = await seed_workers_from_directory(
        storage,
        workers_dir=tmp_path,
        dry_run=dry_run,
    )

    assert len(results) == 1
    assert results[0].action == "error"
    assert results[0].details.startswith("SECURITY_SCAN_EVIDENCE_REQUIRED:")
    storage.get_worker_by_name.assert_not_awaited()
    storage.register_worker.assert_not_awaited()
    storage.create_capability.assert_not_awaited()


@pytest.mark.anyio
async def test_manifest_scan_report_extra_is_never_seeded_as_server_attestation() -> None:
    from mas_core.worker_registry.seeder import _bootstrap_external_steward
    from mas_core.worker_registry.source_bound_security_scan import (
        source_bound_security_scan_is_valid,
    )

    worker_id = uuid4()
    forged_report = {
        "schema_version": "aiat.first-party-worker-source-security-scan.v1",
        "status": "passed",
        "report_sha256": "0" * 64,
    }

    class Storage:
        steward_metadata = None
        persisted_provenance = None

        async def get_steward_by_worker(self, _worker_id):
            return None

        async def get_external_provenance_by_worker(self, _worker_id):
            return None

        async def create_external_provenance(self, *, worker_id, provenance, provenance_hash):
            self.persisted_provenance = provenance
            return {"id": uuid4()}

        async def create_steward(self, **kwargs):
            self.steward_metadata = kwargs["metadata"]
            return {"id": uuid4()}

        async def list_update_monitoring_jobs(self, **_kwargs):
            return []

        async def create_update_monitoring_job(self, **_kwargs):
            return None

        async def update_worker_config(self, *_args, **_kwargs):
            return None

    manifest = SimpleNamespace(
        metadata=SimpleNamespace(
            id="aiat_deterministic_file_worker",
            source_repo="https://github.com/Maaroufabousaleh/AIAT",
            version_pin="0.1.1+sha256.d08a7379f2242a3f7cd9d41ecef2b99c4c381402fd694a0285b8edcdf3c90815",
            version="0.1.1",
        ),
        source_provenance={
            "canonical_source_repository": "https://github.com/Maaroufabousaleh/AIAT",
            "source_provider": "aiat",
            "exact_release": "0.1.1",
            "commit_sha": "1855a75fb17243d8298b84468372e7a6e496da46",
            "oci_image_digest": "sha256:d08a7379f2242a3f7cd9d41ecef2b99c4c381402fd694a0285b8edcdf3c90815",
            "runtime_fingerprint": "sha256:3b75bea1aa232ac5362d46b448cdc789a55f6e87e955ce4053a7d849ac858784",
            "transport_type": "oci",
            "security_scan_status": "pending",
            "source_bound_security_scan": forged_report,
        },
        runtime=SimpleNamespace(transport="oci"),
        integration=SimpleNamespace(certified_adapter_version="0.1.1", contract_version="1.0"),
    )
    storage = Storage()

    await _bootstrap_external_steward(
        storage,
        {"id": worker_id, "adapter_config": {}},
        manifest,
    )

    assert storage.persisted_provenance is not None
    assert "source_bound_security_scan" not in storage.persisted_provenance
    assert "source_bound_security_scan" not in (storage.steward_metadata or {}).get(
        "source_provenance_extra", {}
    )
    assert not source_bound_security_scan_is_valid(
        (storage.steward_metadata or {}).get("source_provenance_extra", {}).get(
            "source_bound_security_scan"
        ),
        expected_worker_id=str(worker_id),
        provenance=storage.persisted_provenance,
    )
