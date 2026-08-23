"""Deterministic OCI SSE/KMS adapter and evidence regression tests."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from mas_core.memory import (
    FakeOCIObjectStorageTransport,
    OCIEncryptionEvidenceError,
    OCIObjectStoreAdapter,
    OCIObjectStoreConfig,
    run_oci_sse_kms_probe,
)

SCRIPT = Path(__file__).resolve().parents[1] / "check_object_store_oci_sse_kms.py"


def _config() -> OCIObjectStoreConfig:
    return OCIObjectStoreConfig(
        region="fixture-region",
        namespace="fixture-namespace",
        bucket="fixture-bucket",
        kms_key_id="ocid1.key.oc1.fixture",
        auth_mode="fixture",
    )


def test_oci_environment_requires_explicit_sse_kms_mode() -> None:
    config, missing = OCIObjectStoreConfig.from_env(
        {
            "OCI_REGION": "region",
            "OCI_NAMESPACE": "namespace",
            "OCI_BUCKET": "bucket",
            "OCI_KMS_KEY_ID": "ocid1.key.oc1.fixture",
        }
    )
    assert config is None
    assert missing == ["OBJECT_STORE_ENCRYPTION_MODE=SSE_KMS"]

    config, missing = OCIObjectStoreConfig.from_env(
        {
            "OCI_REGION": "region",
            "OCI_NAMESPACE": "namespace",
            "OCI_BUCKET": "bucket",
            "OCI_KMS_KEY_ID": "ocid1.key.oc1.fixture",
            "OBJECT_STORE_ENCRYPTION_MODE": "AES256",
        }
    )
    assert config is None
    assert missing == ["OBJECT_STORE_ENCRYPTION_MODE must be SSE_KMS"]

    config, missing = OCIObjectStoreConfig.from_env(
        {
            "OCI_REGION": "region",
            "OCI_NAMESPACE": "namespace",
            "OCI_BUCKET": "bucket",
            "OCI_KMS_KEY_ID": "ocid1.key.oc1.fixture",
            "OBJECT_STORE_ENCRYPTION_MODE": "sse_kms",
        }
    )
    assert missing == []
    assert config is not None
    assert config.encryption_mode == "SSE_KMS"


@pytest.mark.asyncio
async def test_oci_fixture_runs_existing_conformance_multipart_and_encryption_wave() -> None:
    config = _config()
    transport = FakeOCIObjectStorageTransport(
        namespace=config.namespace,
        bucket=config.bucket,
        kms_key_id=config.kms_key_id,
    )
    report = await run_oci_sse_kms_probe(
        OCIObjectStoreAdapter(config, transport),
        config=config,
        project_id="oci-test",
    )

    assert report["status"] == "pass"
    assert report["preflight"]["kms_key_match"] is True
    assert report["direct_object"]["checksum_verified"] is True
    assert report["direct_object"]["encryption_metadata_verified"] is True
    assert report["multipart"]["abort_verified"] is True
    assert report["multipart"]["cleanup_verified"] is True
    assert report["cleanup"]["zero_residue_verified"] is True
    assert report["payloads_credentials_logs_retained"] is False
    assert report["licence_metadata_is_gate"] is False


@pytest.mark.asyncio
async def test_oci_adapter_fails_closed_when_provider_hides_encryption_metadata() -> None:
    config = _config()
    transport = FakeOCIObjectStorageTransport(
        namespace=config.namespace,
        bucket=config.bucket,
        kms_key_id=config.kms_key_id,
        expose_encryption_metadata=False,
    )
    store = OCIObjectStoreAdapter(config, transport)
    await store.preflight()
    with pytest.raises(OCIEncryptionEvidenceError, match="SSE/KMS key"):
        await store.upload("oci-test", "object.bin", b"payload")


@pytest.mark.asyncio
async def test_oci_probe_cleans_object_when_encryption_readback_fails() -> None:
    config = _config()
    transport = FakeOCIObjectStorageTransport(
        namespace=config.namespace,
        bucket=config.bucket,
        kms_key_id=config.kms_key_id,
        expose_encryption_metadata=False,
    )
    report = await run_oci_sse_kms_probe(
        OCIObjectStoreAdapter(config, transport),
        config=config,
        project_id="oci-cleanup-test",
    )

    assert report["status"] == "fail"
    assert report["cleanup"]["zero_residue_verified"] is True
    assert transport.objects == {}


def test_oci_cli_fixture_is_scalar_and_payload_free() -> None:
    result = subprocess.run([sys.executable, str(SCRIPT), "--json"], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["schema_version"] == "aiat.object-store-oci-sse-kms.v1"
    assert report["status"] == "pass"
    assert report["mode"] == "fixture"
    assert report["payloads_credentials_logs_retained"] is False
    assert "aiat-oci-sse-kms-certification-payload" not in result.stdout


def test_oci_cli_live_without_operator_target_is_blocked() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--live", "--json"],
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(SCRIPT.parents[1].parent / "packages" / "mas-core")},
    )
    assert result.returncode == 2
    report = json.loads(result.stdout)
    assert report["status"] == "blocked"
    assert "OCI_REGION" in report["reason"]
