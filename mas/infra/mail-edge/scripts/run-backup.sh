#!/bin/sh
# Create a consistent encrypted backup by quiescing Stalwart file storage.
set -eu

base_dir="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
env_file="${1:-$base_dir/.env.mail-edge}"
test -f "$env_file" || { echo "missing mail-edge environment file" >&2; exit 1; }
export MAIL_EDGE_ENV_FILE="$env_file"

cd "$base_dir"
stalwart_was_running=false
identity_was_running=false
if test -n "$(docker compose --env-file "$env_file" ps --status running -q stalwart)"; then
  stalwart_was_running=true
fi
if test -n "$(docker compose --env-file "$env_file" ps --status running -q identity-service)"; then
  identity_was_running=true
fi

# Quiesce both sides of the identity/mail consistency boundary. PostgreSQL
# remains online solely for the transactionally consistent logical dump.
docker compose --env-file "$env_file" stop identity-service stalwart
restart_services() {
  if test "$stalwart_was_running" = "true"; then
    docker compose --env-file "$env_file" start stalwart >/dev/null
  fi
  if test "$identity_was_running" = "true"; then
    docker compose --env-file "$env_file" start identity-service >/dev/null
  fi
}
trap restart_services EXIT HUP INT TERM
docker compose --env-file "$env_file" --profile backup run --rm encrypted-backup
restart_services
trap - EXIT HUP INT TERM
echo "Encrypted mail-edge backup completed; prior service state restored."
