#!/usr/bin/env bash

# Reconcile the persisted MinIO agent-user password with the current
# environment without printing credentials or touching object data.  MinIO
# keeps IAM users in its data volume, so changing the env file alone does not
# update an already-created ``mas_agent`` user.

set -Eeuo pipefail

MINIO_CONTAINER="${MINIO_CONTAINER:-mas-minio-1}"
AGENT_CONTAINER="${AGENT_CONTAINER:-mas-orchestrator-api-1}"
MC_IMAGE="${MC_IMAGE:-minio/mc@sha256:a7fe349ef4bd8521fb8497f55c6042871b2ae640607cf99d9bede5e9bdf11727}"

if ! docker inspect "$MINIO_CONTAINER" >/dev/null 2>&1; then
  echo "MinIO reconciliation requires a running MinIO container ($MINIO_CONTAINER)" >&2
  exit 2
fi
if ! docker inspect "$AGENT_CONTAINER" >/dev/null 2>&1; then
  echo "MinIO reconciliation requires a running agent container ($AGENT_CONTAINER)" >&2
  exit 2
fi

# Read values from the containers already joined to the private Compose
# network.  This avoids asking Compose to interpolate the entire project (and
# accidentally surfacing unrelated values from a dotenv file).  Values are
# held in a mode-600 temporary env file and are never echoed.
env_value() {
  local container="$1" name="$2"
  docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$container" |
    awk -F= -v wanted="$name" '$1 == wanted {sub(/^[^=]*=/, ""); print; exit}'
}

MINIO_ROOT_USER="$(env_value "$MINIO_CONTAINER" MINIO_ROOT_USER)"
MINIO_ROOT_PASSWORD="$(env_value "$MINIO_CONTAINER" MINIO_ROOT_PASSWORD)"
MINIO_ACCESS_KEY="$(env_value "$AGENT_CONTAINER" MINIO_ACCESS_KEY)"
MINIO_SECRET_KEY="$(env_value "$AGENT_CONTAINER" MINIO_SECRET_KEY)"

if [[ -z "$MINIO_ROOT_USER" || -z "$MINIO_ROOT_PASSWORD" || -z "$MINIO_ACCESS_KEY" || -z "$MINIO_SECRET_KEY" ]]; then
  echo "MinIO reconciliation could not read the required container settings" >&2
  exit 2
fi

CREDENTIAL_FILE="$(mktemp)"
chmod 600 "$CREDENTIAL_FILE"
trap 'rm -f "$CREDENTIAL_FILE"' EXIT
{
  printf 'MINIO_ROOT_USER=%s\n' "$MINIO_ROOT_USER"
  printf 'MINIO_ROOT_PASSWORD=%s\n' "$MINIO_ROOT_PASSWORD"
  printf 'MINIO_ACCESS_KEY=%s\n' "$MINIO_ACCESS_KEY"
  printf 'MINIO_SECRET_KEY=%s\n' "$MINIO_SECRET_KEY"
} >"$CREDENTIAL_FILE"

# Use the MinIO container's network namespace so no host port or Compose
# interpolation is needed.  The operation only upserts one IAM user, attaches
# the existing read/write policy, and performs a read-only postcondition; it
# does not remove users, buckets, or objects.
docker run --rm --network "container:$MINIO_CONTAINER" --env-file "$CREDENTIAL_FILE" --entrypoint /bin/sh "$MC_IMAGE" -eu -c '
  mc alias set local http://127.0.0.1:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null
  mc admin user add local "$MINIO_ACCESS_KEY" "$MINIO_SECRET_KEY" >/dev/null
  mc admin policy attach local readwrite --user "$MINIO_ACCESS_KEY" >/dev/null
  mc admin user info local "$MINIO_ACCESS_KEY" >/dev/null
  echo "minio-agent-user: reconciled (credentials not printed; object data unchanged)"
'
