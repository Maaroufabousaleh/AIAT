"""Storage must bind scan persistence to exact first-party candidate identity."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from mas_core.memory.storage import AgentStorage
from mas_core.worker_registry import source_bound_security_scan as source_scan
from mas_core.worker_registry.source_bound_security_scan import (
    FIRST_PARTY_IMAGE_DIGEST,
    FIRST_PARTY_RUNTIME_SHA256,
    FIRST_PARTY_SOURCE_REPOSITORY,
    FIRST_PARTY_SOURCE_REVISION,
    FIRST_PARTY_WORKER_ID,
    FIRST_PARTY_WORKER_VERSION,
    SourceBoundSecurityScanPersistenceError,
)


class _Rows:
    def __init__(self, value):
        self.value = value

    def mappings(self):
        return self

    def first(self):
        return self.value


def _candidate(worker_id):
    return {
        "worker_registry_id": str(worker_id),
        "manifest_worker_id": FIRST_PARTY_WORKER_ID,
        "version": FIRST_PARTY_WORKER_VERSION,
        "source_repository": FIRST_PARTY_SOURCE_REPOSITORY,
        "source_revision": FIRST_PARTY_SOURCE_REVISION,
        "oci_image_digest": FIRST_PARTY_IMAGE_DIGEST,
        "runtime_sha256": FIRST_PARTY_RUNTIME_SHA256,
        "dockerfile_sha256": "8928573b6889adcbee09ec7cd527d919682254ed725dabc7919a04ef6ccc6d26",
    }


def _storage_with_candidate(
    *,
    image: str,
    runtime_fingerprint: str,
    security_scan_status: str = "pending",
):
    worker_id = uuid4()
    provenance_hash = "p" * 64
    worker = {
        "id": worker_id,
        "name": FIRST_PARTY_WORKER_ID,
        "version": FIRST_PARTY_WORKER_VERSION,
        "version_pin": f"{FIRST_PARTY_WORKER_VERSION}+{FIRST_PARTY_IMAGE_DIGEST.replace(':', '.')}",
        "source_revision": FIRST_PARTY_SOURCE_REVISION,
        "source_repo": FIRST_PARTY_SOURCE_REPOSITORY,
        "adapter_type": "oci",
        "adapter_entrypoint": "ProcessAdapter",
        "sandbox_profile": "sandboxed",
        "adapter_config": {"image": image},
    }
    provenance = {
        "id": uuid4(),
        "worker_id": worker_id,
        "provenance_hash": provenance_hash,
        "canonical_source_repository": FIRST_PARTY_SOURCE_REPOSITORY,
        "source_provider": "aiat",
        "exact_release": FIRST_PARTY_WORKER_VERSION,
        "commit_sha": FIRST_PARTY_SOURCE_REVISION,
        "oci_image_digest": FIRST_PARTY_IMAGE_DIGEST,
        "runtime_fingerprint": runtime_fingerprint,
        "transport_type": "oci",
        "security_scan_status": security_scan_status,
    }
    steward = {"id": uuid4(), "worker_id": worker_id, "metadata": {}}

    async_conn = MagicMock()
    async_conn.execute = AsyncMock(
        side_effect=[_Rows(worker), _Rows(provenance), _Rows(steward), None, None]
    )
    begin = MagicMock()
    begin.__aenter__ = AsyncMock(return_value=async_conn)
    begin.__aexit__ = AsyncMock(return_value=False)
    engine = MagicMock()
    engine.begin.return_value = begin
    storage = AgentStorage("postgresql+asyncpg://localhost/unused")
    storage._engine = engine
    return storage, async_conn, worker_id, provenance_hash


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("image", "runtime_fingerprint"),
    [
        (
            f"untrusted-prefix@{FIRST_PARTY_IMAGE_DIGEST}",
            f"sha256:{FIRST_PARTY_RUNTIME_SHA256}",
        ),
        (
            f"aiat-deterministic-file-worker@{FIRST_PARTY_IMAGE_DIGEST}",
            f"untrusted-prefix-sha256:{FIRST_PARTY_RUNTIME_SHA256}",
        ),
    ],
)
async def test_scan_persistence_rejects_suffix_only_image_or_runtime_match(
    image: str,
    runtime_fingerprint: str,
) -> None:
    storage, conn, worker_id, provenance_hash = _storage_with_candidate(
        image=image,
        runtime_fingerprint=runtime_fingerprint,
    )
    report = {"candidate": _candidate(worker_id), "status": "blocked"}

    with pytest.raises(
        SourceBoundSecurityScanPersistenceError,
        match="SECURITY_SCAN_IDENTITY_INVALID",
    ):
        await storage.persist_source_bound_worker_security_scan(
            worker_id=worker_id,
            report=report,
            expected_provenance_hash=provenance_hash,
        )

    assert conn.execute.await_count == 3


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("initial_status", "expected_status"),
    [
        ("pending", "passed"),
        ("passed", "passed"),
        ("findings_review_required", "findings_review_required"),
        ("requires_human_review", "requires_human_review"),
        ("blocked", "blocked"),
    ],
)
async def test_clean_scan_with_verified_build_binding_promotes_overall_security_gate(
    monkeypatch: pytest.MonkeyPatch,
    initial_status: str,
    expected_status: str,
) -> None:
    storage, conn, worker_id, provenance_hash = _storage_with_candidate(
        image=f"aiat-deterministic-file-worker@{FIRST_PARTY_IMAGE_DIGEST}",
        runtime_fingerprint=f"sha256:{FIRST_PARTY_RUNTIME_SHA256}",
        security_scan_status=initial_status,
    )
    report = {
        "candidate": _candidate(worker_id),
        "status": "passed",
        "finding_count": 0,
        "scanner_error_count": 0,
        "report_sha256": "r" * 64,
    }
    monkeypatch.setattr(
        source_scan,
        "source_bound_security_scan_report_integrity_is_valid",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        source_scan,
        "source_bound_security_scan_is_valid",
        lambda *_args, **_kwargs: True,
    )
    result = await storage.persist_source_bound_worker_security_scan(
        worker_id=worker_id,
        report=report,
        expected_provenance_hash=provenance_hash,
    )

    assert result is not None
    assert result["security_scan_status"] == expected_status
    assert result["source_bound_security_scan_status"] == "passed"
    assert len(conn.execute.await_args_list) == 5
    steward_update = conn.execute.await_args_list[3].args[0]
    provenance_update = conn.execute.await_args_list[4].args[0]

    def _values(statement):
        return {
            key if isinstance(key, str) else key.name: getattr(value, "value", value)
            for key, value in statement._values.items()
        }

    steward_values = _values(steward_update)
    provenance_values = _values(provenance_update)
    assert (
        steward_values["metadata"]["source_provenance_extra"][
            "source_bound_security_scan_status"
        ]
        == "passed"
    )
    assert provenance_values["security_scan_status"] == expected_status
