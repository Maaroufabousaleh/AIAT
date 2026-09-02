"""Run OCI Object Storage customer-managed SSE/KMS evidence.

Fixture mode is deterministic and exercises the same adapter/evidence path as
live mode.  ``--live`` requires a real OCI target, a governed OCI SDK auth
reference, and the non-secret ``OCI_*`` identifiers.  Install the optional SDK
with ``uv run --package mas-core --extra oci`` before a live run.  Missing
live state is a blocked external prerequisite; it is never converted into a
provider pass.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os

from mas_core.memory import (
    FakeOCIObjectStorageTransport,
    OCIObjectStorageSdkTransport,
    OCIObjectStoreAdapter,
    OCIObjectStoreConfig,
    run_oci_sse_kms_probe,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit machine-readable evidence")
    parser.add_argument("--live", action="store_true", help="use the operator-provided OCI target")
    parser.add_argument("--project-id", default=os.getenv("OCI_EVIDENCE_PROJECT", "aiat-oci-sse-kms-certification"))
    return parser


async def _run(args: argparse.Namespace) -> dict[str, object]:
    config, missing = OCIObjectStoreConfig.from_env()
    if args.live:
        if missing or config is None:
            return {
                "schema_version": "aiat.object-store-oci-sse-kms.v1",
                "mode": "live",
                "status": "blocked",
                "provider": "oci-object-storage",
                "reason": f"missing live configuration: {', '.join(missing)}",
                "required_non_secret_config": [
                    "OCI_REGION",
                    "OCI_NAMESPACE",
                    "OCI_BUCKET",
                    "OCI_KMS_KEY_ID",
                    "OBJECT_STORE_ENCRYPTION_MODE=SSE_KMS",
                ],
                "required_secret_references": ["OCI_CONFIG_FILE + OCI_AUTH_PROFILE or OCI_AUTH_MODE=instance_principal"],
                "payloads_credentials_logs_retained": False,
                "licence_metadata_is_gate": False,
            }
        try:
            transport = OCIObjectStorageSdkTransport(config)
        except Exception as exc:
            return {
                "schema_version": "aiat.object-store-oci-sse-kms.v1",
                "mode": "live",
                "status": "blocked",
                "provider": "oci-object-storage",
                "reason": f"OCI client unavailable: {type(exc).__name__}",
                "payloads_credentials_logs_retained": False,
                "licence_metadata_is_gate": False,
            }
        store = OCIObjectStoreAdapter(config, transport)
        return await run_oci_sse_kms_probe(store, config=config, project_id=str(args.project_id))

    fixture_config = OCIObjectStoreConfig(
        region="fixture-region",
        namespace="fixture-namespace",
        bucket="fixture-bucket",
        kms_key_id="ocid1.key.oc1.fixture",
        auth_profile="fixture",
        auth_mode="fixture",
    )
    transport = FakeOCIObjectStorageTransport(
        namespace=fixture_config.namespace,
        bucket=fixture_config.bucket,
        kms_key_id=fixture_config.kms_key_id,
    )
    store = OCIObjectStoreAdapter(fixture_config, transport)
    return await run_oci_sse_kms_probe(store, config=fixture_config, project_id=str(args.project_id))


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = asyncio.run(_run(args))
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        print(f"object-store-oci-sse-kms: {report.get('status', 'fail').upper()}")
        if report.get("reason"):
            print(f"  {report['reason']}")
    return 0 if report.get("status") == "pass" else 2 if report.get("status") == "blocked" else 1


if __name__ == "__main__":
    raise SystemExit(main())
