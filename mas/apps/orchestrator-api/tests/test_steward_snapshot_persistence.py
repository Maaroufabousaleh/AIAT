"""Snapshot identity must survive storage and steward rehydration."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

import pytest
from starlette.requests import Request

from mas_core.worker_registry.steward import (
    ExternalProvenance,
    ExternalWorkerSteward,
    StewardStatus,
)


class _SnapshotStorage:
    """Small durable-row double for the steward snapshot route lifecycle."""

    def __init__(self, worker_id: UUID, steward: ExternalWorkerSteward) -> None:
        self.worker_id = worker_id
        self.steward = steward
        self.sources: dict[str, dict] = {}
        self.documentation_rows: list[dict] = []
        self.capability_rows: list[dict] = []
        self.worker = {"id": worker_id, "name": "snapshot_test_worker"}

    async def get_worker(self, worker_id: UUID) -> dict | None:
        return self.worker if worker_id == self.worker_id else None

    async def get_documentation_source(self, *, steward_id: UUID, uri: str) -> dict | None:
        assert steward_id == self.steward.steward_id
        return self.sources.get(uri)

    async def create_documentation_source(
        self,
        *,
        steward_id: UUID,
        uri: str,
        source_type: str,
        trusted_for_provenance: bool,
        allowed_domains: list[str],
        source_id: UUID,
    ) -> dict:
        row = {
            "id": source_id,
            "steward_id": steward_id,
            "uri": uri,
            "source_type": source_type,
            "trusted_for_provenance": trusted_for_provenance,
            "allowed_domains": allowed_domains,
        }
        self.sources[uri] = row
        return row

    async def create_documentation_snapshot(
        self,
        *,
        source_id: UUID,
        version: str,
        content_sha256: str,
        content_ref: str | None,
        extracted_interfaces: dict,
        security_findings: list[str],
        untrusted: bool,
        snapshot_id: UUID,
        captured_at: datetime,
    ) -> dict:
        row = {
            "id": snapshot_id,
            "source_id": source_id,
            "version": version,
            "content_sha256": content_sha256,
            "content_ref": content_ref,
            "extracted_interfaces": extracted_interfaces,
            "security_findings": security_findings,
            "untrusted": untrusted,
            "captured_at": captured_at,
        }
        self.documentation_rows.append(row)
        return row

    async def create_capability_snapshot(
        self,
        *,
        worker_id: UUID,
        steward_id: UUID,
        version: str,
        capabilities: dict,
        evidence_refs: list[str],
        snapshot_id: UUID,
        discovered_at: datetime,
    ) -> dict:
        row = {
            "id": snapshot_id,
            "worker_id": worker_id,
            "steward_id": steward_id,
            "version": version,
            "capabilities_json": capabilities,
            "evidence_refs": evidence_refs,
            "created_at": discovered_at,
        }
        self.capability_rows.append(row)
        return row

    async def get_steward_by_worker(self, worker_id: UUID) -> dict | None:
        if worker_id != self.worker_id:
            return None
        return {
            "id": self.steward.steward_id,
            "worker_id": worker_id,
            "status": StewardStatus.READY.value,
            "active_skill_bundle_id": None,
            "active_adapter_id": None,
        }

    async def get_external_provenance_by_worker(self, worker_id: UUID) -> dict | None:
        if worker_id != self.worker_id:
            return None
        return self.steward.provenance.model_dump(mode="json")

    async def list_documentation_snapshots(self, steward_id: UUID) -> list[dict]:
        assert steward_id == self.steward.steward_id
        rows = []
        for row in self.documentation_rows:
            source = next(item for item in self.sources.values() if item["id"] == row["source_id"])
            rows.append(
                {
                    **row,
                    "source_uri": source["uri"],
                    "source_type": source["source_type"],
                    "source_trusted": source["trusted_for_provenance"],
                    "source_allowed_domains": source["allowed_domains"],
                }
            )
        return rows

    async def list_capability_snapshots(
        self, worker_id: UUID, *, steward_id: UUID | None = None
    ) -> list[dict]:
        return [
            row
            for row in self.capability_rows
            if row["worker_id"] == worker_id
            and (steward_id is None or row["steward_id"] == steward_id)
        ]

    async def list_compatibility_matrices(self, _worker_id: UUID) -> list[dict]:
        return []

    async def list_skill_bundle_candidates(self, _worker_id: UUID) -> list[dict]:
        return []

    async def list_certification_runs(self, _worker_id: UUID) -> list[dict]:
        return []

    async def list_rollout_records(self, _worker_id: UUID) -> list[dict]:
        return []


def _operator_request() -> Request:
    request = Request({"type": "http", "headers": []})
    request.state.aiat_auth_principal = "operator"
    return request


@pytest.mark.asyncio
async def test_snapshot_ids_and_times_survive_routes_rehydration_and_candidate_creation(
    monkeypatch,
) -> None:
    from orchestrator_api import main

    worker_id = uuid4()
    steward_id = uuid4()
    persisted_source_id = uuid4()
    stale_runtime_source_id = uuid4()
    provenance = ExternalProvenance(
        canonical_source_repository="https://example.invalid/snapshot-worker",
        exact_release="1.0.0",
        transport_type="http",
        security_scan_status="passed",
    )
    steward = ExternalWorkerSteward(
        worker_id=str(worker_id),
        steward_id=steward_id,
        provenance=provenance,
        status=StewardStatus.READY,
    )
    # Simulate a cached source object whose ID predates the durable row.
    from mas_core.worker_registry.steward import DocumentationSource

    steward.documentation_sources.append(
        DocumentationSource(
            source_id=stale_runtime_source_id,
            uri="https://example.invalid/docs",
            trusted=False,
        )
    )
    storage = _SnapshotStorage(worker_id, steward)
    storage.sources["https://example.invalid/docs"] = {
        "id": persisted_source_id,
        "steward_id": steward_id,
        "uri": "https://example.invalid/docs",
        "source_type": "official",
        "trusted_for_provenance": False,
        "allowed_domains": [],
    }
    monkeypatch.setattr(main, "_storage", lambda: storage)
    cache_key = str(worker_id)
    main._worker_steward_runtimes.pop(cache_key, None)
    main._worker_steward_runtimes[cache_key] = steward

    try:
        documentation = await main.add_steward_documentation(
            worker_id,
            main.DocumentationSnapshotRequest(
                uri="https://example.invalid/docs",
                version="1.0",
                content_sha256="a" * 64,
            ),
            _operator_request(),
        )
        capabilities = await main.record_steward_capabilities(
            worker_id,
            {"version": "1.0", "capabilities": {}},
            _operator_request(),
        )

        doc_row = storage.documentation_rows[0]
        capability_row = storage.capability_rows[0]
        assert storage.sources["https://example.invalid/docs"]["id"] == persisted_source_id
        assert doc_row["source_id"] == persisted_source_id
        assert documentation["snapshot"]["source"]["source_id"] == str(persisted_source_id)
        assert documentation["snapshot"]["snapshot_id"] == str(doc_row["id"])
        assert datetime.fromisoformat(documentation["snapshot"]["captured_at"]) == doc_row[
            "captured_at"
        ]
        assert capabilities["snapshot_id"] == str(capability_row["id"])
        assert datetime.fromisoformat(capabilities["discovered_at"]) == capability_row[
            "created_at"
        ]

        # A newly introduced source must also use the ID generated in memory.
        new_uri = "https://example.invalid/new-docs"
        created = await main.add_steward_documentation(
            worker_id,
            main.DocumentationSnapshotRequest(
                uri=new_uri,
                version="1.0",
                content_sha256="b" * 64,
            ),
            _operator_request(),
        )
        new_row = storage.documentation_rows[1]
        assert storage.sources[new_uri]["id"] == UUID(created["snapshot"]["source"]["source_id"])
        assert new_row["source_id"] == storage.sources[new_uri]["id"]
        assert new_row["id"] == UUID(created["snapshot"]["snapshot_id"])

        main._worker_steward_runtimes.pop(cache_key, None)
        rehydrated = await main._steward_runtime(storage, worker_id)
        assert rehydrated is not None
        candidate = rehydrated.generate_candidate(
            semantic_version="1.0.0",
            adapter_version="1.0.0",
            upstream_compatibility_range="==1.0.0",
        )
        assert candidate.bundle.documentation_refs == tuple(
            UUID(str(row["id"])) for row in storage.documentation_rows
        )
        assert candidate.bundle.verified_capabilities is not None
        assert candidate.bundle.verified_capabilities.snapshot_id == capability_row["id"]
        assert candidate.bundle.verified_capabilities.discovered_at == capability_row["created_at"]
        assert candidate.bundle.source_provenance.last_verified_documentation_at == new_row[
            "captured_at"
        ]
    finally:
        main._worker_steward_runtimes.pop(cache_key, None)
