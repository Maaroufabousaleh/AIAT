#!/usr/bin/env bash

# Run the provider-neutral object-store contract inside the running
# orchestrator container, which can reach MinIO on the private Compose
# network.  The report is JSON and contains no credential values.

set -Eeuo pipefail

MINIO_CONTAINER="${MINIO_CONTAINER:-mas-minio-1}"
AGENT_CONTAINER="${AGENT_CONTAINER:-mas-orchestrator-api-1}"
PROJECT_ID="${1:-aiat-conformance-live-roadmap}"

if ! docker inspect -f '{{.State.Running}}' "$AGENT_CONTAINER" 2>/dev/null | grep -qx true; then
  echo "MinIO conformance requires a running agent container ($AGENT_CONTAINER)" >&2
  exit 2
fi
if ! docker inspect -f '{{.State.Running}}' "$MINIO_CONTAINER" 2>/dev/null | grep -qx true; then
  echo "MinIO conformance requires a running MinIO container ($MINIO_CONTAINER)" >&2
  exit 2
fi

# The code runs in the agent container so its existing endpoint and credential
# boundary are used without exposing them to the host shell or a Compose
# interpolation pass.  The project ID is the only caller-controlled value and
# is validated by BlobClient's normal path rules.
docker exec -i -e "AIAT_CONFORMANCE_PROJECT_ID=$PROJECT_ID" "$AGENT_CONTAINER" sh -lc 'PYTHONPATH=/app/mas_core python -' <<'PY'
import asyncio
import json
import os

from mas_core.memory import BlobClient, run_object_store_conformance


async def run():
    endpoint = os.environ.get("MINIO_ENDPOINT")
    access_key = os.environ.get("MINIO_ACCESS_KEY")
    secret_key = os.environ.get("MINIO_SECRET_KEY")
    bucket = os.environ.get("MINIO_BUCKET", "mas-agents")
    missing = [
        name
        for name, value in (
            ("endpoint", endpoint),
            ("access_key", access_key),
            ("secret_key", secret_key),
        )
        if not value
    ]
    if missing:
        return {
            "schema_version": "aiat.object-store-conformance.v1",
            "mode": "local-live",
            "adapter_type": "s3-compatible",
            "status": "blocked",
            "reason": f"missing live configuration: {', '.join(missing)}",
        }

    client = BlobClient(
        endpoint,
        access_key=access_key,
        secret_key=secret_key,
        bucket=bucket,
        region=os.environ.get("AIAT_OBJECT_STORE_REGION", "us-east-1"),
    )
    try:
        await client.connect()
        report = await run_object_store_conformance(
            client,
            project_id=os.environ["AIAT_CONFORMANCE_PROJECT_ID"],
            bucket=bucket,
        )
        result = report.as_dict()
        result.update({"mode": "local-live", "provider": "minio"})
        return result
    except Exception as exc:
        return {
            "schema_version": "aiat.object-store-conformance.v1",
            "mode": "local-live",
            "adapter_type": "s3-compatible",
            "status": "blocked",
            "provider": "minio",
            "reason": f"local provider unavailable: {type(exc).__name__}: {exc}",
        }
    finally:
        await client.close()


result = asyncio.run(run())
print(json.dumps(result, sort_keys=True))
if result.get("status") == "blocked":
    raise SystemExit(2)
raise SystemExit(0 if result.get("passed") else 1)
PY
