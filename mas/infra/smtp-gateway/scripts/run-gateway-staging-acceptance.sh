#!/bin/sh
set -eu

base_dir="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
repo_root="$(CDPATH= cd -- "$base_dir/../.." && pwd)"
env_file="${1:-$base_dir/.env.smtp-gateway}"
test "${AIAT_RUN_LIVE_GATEWAY_TESTS:-}" = 1 || { echo "set AIAT_RUN_LIVE_GATEWAY_TESTS=1 on approved staging only" >&2; exit 2; }
test "${LIVE_EXTERNAL_SENDER_CONFIRMED:-}" = 1 || { echo "LIVE_EXTERNAL_SENDER_CONFIRMED=1 is required when the sender is outside the gateway/home network" >&2; exit 1; }
test -f "$env_file" || { echo "missing SMTP gateway environment file" >&2; exit 1; }
command -v pytest >/dev/null 2>&1 || { echo "pytest is required" >&2; exit 1; }

env_value() { awk -F= -v key="$1" '$1 == key {sub(/^[^=]*=/, ""); sub(/\r$/, ""); print; exit}' "$env_file"; }
test "${LIVE_MAIL_HOST:-}" = "$(env_value MAIL_HOSTNAME)" || { echo "LIVE_MAIL_HOST must be the gateway mail hostname" >&2; exit 1; }
test "${LIVE_SMTP_PORT:-25}" = 25 || { echo "LIVE_SMTP_PORT must be 25" >&2; exit 1; }
test "${LIVE_IDENTITY_SERVICE_URL:-}" = "https://$(env_value IDENTITY_HOSTNAME)" || { echo "LIVE_IDENTITY_SERVICE_URL must be the gateway HTTPS identity hostname" >&2; exit 1; }
evidence_file="${GATEWAY_STAGING_EVIDENCE:-$(env_value GATEWAY_STAGING_EVIDENCE)}"
mkdir -p "$(dirname "$evidence_file")"
export PYTHONPATH="$repo_root/apps/identity-service${PYTHONPATH:+:$PYTHONPATH}"

AIAT_RUN_LIVE_IDENTITY_TESTS=1 \
  LIVE_MAIL_HOST="$(env_value MAIL_HOSTNAME)" \
  LIVE_SMTP_PORT=25 \
  LIVE_IDENTITY_SERVICE_URL="https://$(env_value IDENTITY_HOSTNAME)" \
  pytest "$repo_root/apps/identity-service/tests/test_live_identity_acceptance.py" -m live -q

umask 077
{
  echo "E2E_SMTP_JMAP=PASS"
  echo "GATEWAY_TO_STALWART_SMTP=PASS"
  echo "RESEND_RELAY_CERTIFIED=PASS"
} >>"$evidence_file"
echo "live gateway staging acceptance passed and evidence was recorded; this was an opt-in real-infrastructure test."
