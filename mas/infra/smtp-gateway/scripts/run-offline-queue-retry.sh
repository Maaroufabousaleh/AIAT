#!/bin/sh
set -eu

base_dir="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
env_file="${1:-$base_dir/.env.smtp-gateway}"
test "${AIAT_RUN_LIVE_GATEWAY_TESTS:-}" = 1 || { echo "set AIAT_RUN_LIVE_GATEWAY_TESTS=1 on approved staging only" >&2; exit 2; }
for name in HOME_SSH_TARGET HOME_REMOTE_COMPOSE_DIR HOME_REMOTE_MAIL_ENV_FILE HOME_REMOTE_GATEWAY_OVERRIDE_FILE GATEWAY_TEST_RECIPIENT GATEWAY_TEST_ENVELOPE_FROM JMAP_COUNT_COMMAND; do
  value="$(printenv "$name" 2>/dev/null || true)"
  test -n "$value" || { echo "$name is required for the offline queue test" >&2; exit 1; }
done
command -v ssh >/dev/null 2>&1 || { echo "ssh is required" >&2; exit 1; }
command -v swaks >/dev/null 2>&1 || { echo "swaks is required" >&2; exit 1; }
command -v docker >/dev/null 2>&1 || { echo "docker is required on the gateway" >&2; exit 1; }

env_value() { awk -F= -v key="$1" '$1 == key {sub(/^[^=]*=/, ""); sub(/\r$/, ""); print; exit}' "$env_file"; }
gateway_host="$(env_value MAIL_HOSTNAME)"
evidence_file="${GATEWAY_STAGING_EVIDENCE:-$(env_value GATEWAY_STAGING_EVIDENCE)}"
token="aiat-offline-$(date +%s)-$$"
export GATEWAY_OFFLINE_TOKEN="$token"
stopped=0
cleanup() {
  if [ "$stopped" -eq 1 ]; then
    ssh "$HOME_SSH_TARGET" "cd '$HOME_REMOTE_COMPOSE_DIR' && docker compose --env-file '$HOME_REMOTE_MAIL_ENV_FILE' -f docker-compose.yml -f '$HOME_REMOTE_GATEWAY_OVERRIDE_FILE' up -d stalwart" >/dev/null
  fi
}
trap cleanup EXIT INT TERM

ssh "$HOME_SSH_TARGET" "cd '$HOME_REMOTE_COMPOSE_DIR' && docker compose --env-file '$HOME_REMOTE_MAIL_ENV_FILE' -f docker-compose.yml -f '$HOME_REMOTE_GATEWAY_OVERRIDE_FILE' stop stalwart"
stopped=1
swaks --server "$gateway_host" --port 25 --from "$GATEWAY_TEST_ENVELOPE_FROM" --to "$GATEWAY_TEST_RECIPIENT" --header "Subject: $token" --body "offline queue retry $token" --timeout 15
sleep "${GATEWAY_QUEUE_SETTLE_SECONDS:-8}"
queued="$(docker compose --env-file "$env_file" -f "$base_dir/docker-compose.yml" exec -T postfix-gateway aiat-gateway-queue-status)"
printf '%s\n' "$queued" | awk -F'"queue_depth":' 'NF > 1 {split($2, a, ","); if (a[1] + 0 > 0) ok=1} END {exit(ok ? 0 : 1)}' || { echo "offline test failed: message did not remain queued while home Stalwart was stopped" >&2; exit 1; }

ssh "$HOME_SSH_TARGET" "cd '$HOME_REMOTE_COMPOSE_DIR' && docker compose --env-file '$HOME_REMOTE_MAIL_ENV_FILE' -f docker-compose.yml -f '$HOME_REMOTE_GATEWAY_OVERRIDE_FILE' up -d stalwart"
stopped=0
deadline=$(( $(date +%s) + ${GATEWAY_RETRY_TIMEOUT_SECONDS:-180} ))
while [ "$(date +%s)" -lt "$deadline" ]; do
  queued="$(docker compose --env-file "$env_file" -f "$base_dir/docker-compose.yml" exec -T postfix-gateway aiat-gateway-queue-status 2>/dev/null || true)"
  if printf '%s\n' "$queued" | awk -F'"queue_depth":' 'NF > 1 {split($2, a, ","); if (a[1] + 0 == 0) ok=1} END {exit(ok ? 0 : 1)}'; then break; fi
  sleep 5
done
printf '%s\n' "$queued" | awk -F'"queue_depth":' 'NF > 1 {split($2, a, ","); if (a[1] + 0 == 0) ok=1} END {exit(ok ? 0 : 1)}' || { echo "offline test failed: queued message was not delivered after reconnect" >&2; exit 1; }

count="$(sh -c "$JMAP_COUNT_COMMAND")"
test "$count" = 1 || { echo "offline test failed: JMAP verification expected exactly one message for $token, got $count" >&2; exit 1; }
mkdir -p "$(dirname "$evidence_file")"
umask 077
echo "GATEWAY_QUEUE_PERSISTENCE=PASS" >>"$evidence_file"
echo "OFFLINE_QUEUE_RETRY=PASS" >>"$evidence_file"
echo "offline queue persistence, retry, and exactly-once JMAP count passed for token $token"
