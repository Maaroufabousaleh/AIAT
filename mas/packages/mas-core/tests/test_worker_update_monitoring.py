"""Tests for non-authoritative external-worker update monitoring."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from mas_core.worker_registry.monitoring import run_due_update_monitors


class _Storage:
    def __init__(self) -> None:
        self.worker_id = uuid4()
        self.steward_id = uuid4()
        self.job_id = uuid4()
        self.adapter_id = uuid4()
        self.bundle_id = uuid4()
        self.candidates: list[dict] = []
        self.adapters: list[dict] = []
        self.bundles: list[dict] = []
        self.results: list[dict] = []
        self.active_pointer_mutations = 0

    async def list_update_monitoring_jobs(self, *, worker_id=None, limit=1000):
        if worker_id is not None and worker_id != self.worker_id:
            return []
        return [{
            "id": self.job_id,
            "worker_id": self.worker_id,
            "steward_id": self.steward_id,
            "cadence": "daily",
            "status": "active",
            "last_checked_at": None,
        }]

    async def get_worker(self, worker_id):
        assert worker_id == self.worker_id
        return {
            "id": self.worker_id,
            "source_repo": "https://github.com/example/worker",
            "source_revision": "main",
            "upstream_commit_sha": "a" * 40,
            "adapter_type": "http",
            "isolation_mode": "external",
            "adapter_config": {"entrypoint": "worker.main"},
        }

    async def get_external_provenance_by_worker(self, worker_id):
        assert worker_id == self.worker_id
        return {
            "canonical_source_repository": "https://github.com/example/worker",
            "source_provider": "github",
            "commit_sha": "a" * 40,
            "transport_type": "http",
            "protocol_api_version": "aiat.worker.v1",
            "adapter_version": "1.0.0",
            "license_id": "MIT",
            "redistribution_status": "approved",
            "security_scan_status": "passed",
            "verified_at": datetime(2026, 7, 20, tzinfo=UTC),
        }

    async def list_skill_bundle_candidates(self, worker_id):
        assert worker_id == self.worker_id
        return list(self.candidates)

    async def create_runtime_adapter(self, **kwargs):
        self.adapter_kwargs = kwargs
        row = {"id": self.adapter_id, **kwargs}
        self.adapters.append(row)
        return row

    async def list_runtime_adapters(self, worker_id, *, status=None):
        assert worker_id == self.worker_id
        return [
            row for row in self.adapters
            if status is None or row.get("status") == status
        ]

    async def create_skill_bundle(self, **kwargs):
        self.bundle_kwargs = kwargs
        row = {"id": self.bundle_id, **kwargs}
        self.bundles.append(row)
        return row

    async def list_skill_bundles(self, worker_id, *, steward_id=None):
        assert worker_id == self.worker_id
        assert steward_id in {None, self.steward_id}
        return list(self.bundles)

    async def create_skill_bundle_candidate(self, **kwargs):
        self.candidates.append({"id": kwargs["candidate_id"], "diff_json": kwargs["diff"]})
        self.candidate_kwargs = kwargs
        return kwargs

    async def record_update_monitoring_result(self, job_id, **kwargs):
        assert job_id == self.job_id
        self.results.append(kwargs)
        return kwargs

    async def update_steward(self, steward_id, **kwargs):
        assert steward_id == self.steward_id
        self.steward_kwargs = kwargs


@pytest.mark.anyio
async def test_monitor_creates_review_only_discovered_candidate_and_never_promotes():
    storage = _Storage()

    async def checker(**kwargs):
        assert kwargs["source_repo"] == "https://github.com/example/worker"
        return {
            "has_updates": True,
            "latest_commit": "b" * 40,
            "current_commit": "a" * 40,
        }

    results = await run_due_update_monitors(
        storage,
        checker=checker,
        now=datetime(2026, 7, 21, tzinfo=UTC),
    )

    assert results[0]["status"] == "candidate_discovered"
    assert storage.candidate_kwargs["intake_status"] == "DISCOVERED"
    assert storage.bundle_kwargs["status"] == "DRAFT"
    assert storage.adapter_kwargs["status"] == "candidate"
    assert storage.candidate_kwargs["evidence"]["activation_prohibited"] is True
    assert storage.results[-1]["last_candidate_id"] == storage.candidate_kwargs["candidate_id"]
    assert storage.bundle_kwargs["provenance"]["verified_at"] == "2026-07-20T00:00:00+00:00"
    assert storage.active_pointer_mutations == 0


@pytest.mark.anyio
async def test_monitor_deduplicates_an_observed_commit():
    storage = _Storage()
    existing_id = uuid4()
    storage.candidates = [{
        "id": existing_id,
        "diff_json": {"monitoring": {"latest_commit": "b" * 40}},
    }]

    async def checker(**_kwargs):
        return {"has_updates": True, "latest_commit": "b" * 40}

    results = await run_due_update_monitors(
        storage,
        checker=checker,
        force_worker_id=storage.worker_id,
    )

    assert results[0]["candidate_id"] == str(existing_id)
    assert not hasattr(storage, "adapter_kwargs")
    assert not hasattr(storage, "bundle_kwargs")


@pytest.mark.anyio
async def test_monitor_recovers_from_a_partially_persisted_candidate_adapter():
    storage = _Storage()
    latest_commit = "b" * 40
    version = f"upstream-{latest_commit[:12]}"
    adapter_hash = hashlib.sha256(
        json.dumps(
            {
                "worker_id": str(storage.worker_id),
                "version": version,
                "transport": "http",
                "latest_commit": latest_commit,
                "entrypoint": "worker.main",
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    storage.adapters = [{
        "id": storage.adapter_id,
        "version": version,
        "status": "candidate",
        "content_hash": adapter_hash,
    }]

    async def checker(**_kwargs):
        return {"has_updates": True, "latest_commit": latest_commit}

    results = await run_due_update_monitors(
        storage,
        checker=checker,
        force_worker_id=storage.worker_id,
    )

    assert results[0]["status"] == "candidate_discovered"
    assert storage.candidate_kwargs["adapter_id"] == storage.adapter_id
    assert not hasattr(storage, "adapter_kwargs")


@pytest.mark.anyio
async def test_monitor_uses_immutable_provenance_when_worker_source_is_mutated():
    storage = _Storage()

    async def mutated_worker(_worker_id):
        row = await _Storage.get_worker(storage, storage.worker_id)
        row["source_repo"] = "https://example.invalid/unreviewed-repository"
        row["source_revision"] = "unreviewed-branch"
        return row

    storage.get_worker = mutated_worker  # type: ignore[method-assign]

    async def checker(**kwargs):
        assert kwargs["source_repo"] == "https://github.com/example/worker"
        assert kwargs["current_revision"] == "a" * 40
        assert kwargs["current_commit"] == "a" * 40
        return {"has_updates": False, "latest_commit": "a" * 40}

    results = await run_due_update_monitors(storage, checker=checker, force_worker_id=storage.worker_id)

    assert results[0]["status"] == "no_change"
    assert storage.results[-1]["status"] == "active"
