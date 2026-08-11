"""Provider-neutral object-store conformance fixture tests."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from argparse import Namespace
from dataclasses import replace
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

from mas_core.memory import (
    OBJECT_STORE_CONFORMANCE_SCHEMA,
    BlobClient,
    BlobRef,
    InMemoryObjectStore,
    run_object_store_conformance,
)


SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "check_object_store_conformance.py"


def _load_runner():
    spec = spec_from_file_location("object_store_conformance_runner", SCRIPT)
    assert spec and spec.loader
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_s3_blob_client_exposes_stable_conformance_identity() -> None:
    assert BlobClient.adapter_type == "s3-compatible"
    assert BlobClient.adapter_version == "aioboto3"


@pytest.mark.asyncio
async def test_live_conformance_runner_fails_closed_without_provider_configuration() -> None:
    env = os.environ.copy()
    for key in ("AIAT_OBJECT_STORE_ENDPOINT", "AIAT_OBJECT_STORE_ACCESS_KEY", "AIAT_OBJECT_STORE_SECRET_KEY"):
        env.pop(key, None)
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parents[3] / "scripts" / "check_object_store_conformance.py"),
            "--live",
            "--json",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    report = json.loads(result.stdout)
    assert result.returncode == 2
    assert report["status"] == "blocked"
    assert "missing live configuration" in report["reason"]


@pytest.mark.asyncio
async def test_in_memory_fixture_passes_the_full_object_store_contract() -> None:
    report = await run_object_store_conformance(InMemoryObjectStore())

    assert report.schema_version == OBJECT_STORE_CONFORMANCE_SCHEMA
    assert report.passed is True
    assert report.counts == {"PASS": 8, "FAIL": 0}
    assert {
        "upload_reference",
        "download_round_trip",
        "integrity_mismatch_rejection",
        "empty_object_round_trip",
        "project_scope_listing",
        "exists_delete_list",
        "path_validation",
        "cleanup",
    } == {case.case_id for case in report.cases}


@pytest.mark.asyncio
async def test_object_store_report_is_stable_for_the_same_fixture() -> None:
    first = (await run_object_store_conformance(InMemoryObjectStore())).as_dict()
    second = (await run_object_store_conformance(InMemoryObjectStore())).as_dict()

    assert first == second


@pytest.mark.asyncio
async def test_integrity_mismatch_is_a_required_contract_failure() -> None:
    class NoIntegrityStore(InMemoryObjectStore):
        async def download(self, ref):  # type: ignore[no-untyped-def]
            payload, _content_type = self._objects[(ref.bucket, ref.key)]
            return payload

    report = await run_object_store_conformance(NoIntegrityStore())

    assert report.passed is False
    mismatch = next(case for case in report.cases if case.case_id == "integrity_mismatch_rejection")
    assert mismatch.status == "FAIL"


@pytest.mark.asyncio
async def test_fixture_download_rejects_tampered_reference() -> None:
    store = InMemoryObjectStore()
    ref = await store.upload("project", "artifact.bin", b"payload")

    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        await store.download(replace(ref, sha256="0" * 64))


@pytest.mark.asyncio
async def test_fixture_download_rejects_tampered_size_reference() -> None:
    store = InMemoryObjectStore()
    ref = await store.upload("project", "artifact.bin", b"payload")

    with pytest.raises(ValueError, match="size mismatch"):
        await store.download(replace(ref, size_bytes=ref.size_bytes + 1))


@pytest.mark.asyncio
async def test_s3_blob_client_rejects_declared_size_mismatch() -> None:
    blob = BlobClient(
        endpoint_url="http://minio:9000",
        access_key="test",
        secret_key="test",
    )
    class Body:
        async def read(self) -> bytes:
            return b"payload"

    body = Body()

    class FakeClient:
        async def get_object(self, **_kwargs):
            return {"Body": body}

    blob._client = FakeClient()
    payload_sha = hashlib.sha256(b"payload").hexdigest()

    with pytest.raises(ValueError, match="size mismatch"):
        await blob.download(
            BlobRef(
                bucket="mas-agents",
                key="project/artifact.bin",
                sha256=payload_sha,
                size_bytes=len(b"payload") + 1,
            )
        )


def test_compose_local_probe_normalizes_private_network_report(monkeypatch) -> None:
    runner = _load_runner()
    monkeypatch.setattr(runner.shutil, "which", lambda name: "/usr/bin/docker")

    class _Result:
        returncode = 0
        stdout = json.dumps(
            {
                "schema_version": "aiat.object-store-conformance.v1",
                "mode": "local-live",
                "adapter_type": "s3-compatible",
                "status": "pass",
                "passed": True,
                "counts": {"PASS": 8, "FAIL": 0},
            }
        )
        stderr = ""

    monkeypatch.setattr(runner.subprocess, "run", lambda *args, **kwargs: _Result())
    report = runner._run_compose_local(Namespace(project_id="aiat-test"))

    assert report["status"] == "pass"
    assert report["provider"] == "minio"
    assert report["transport"] == "docker-exec-private-network"
    assert report["counts"] == {"PASS": 8, "FAIL": 0}
