#!/bin/sh
# Read-only local WSL preflight for one-message Resend certification.
set -eu

base_dir="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
profile="${1:-}"
secret_file=""
relay_secret_file=""
container=""
account_id=""
sender=""
jmap_url="http://127.0.0.1:18080"
admin_url="http://127.0.0.1:18080/api"
output=""

usage() {
  echo "usage: $0 PROFILE --secret-file FILE [--relay-secret-file FILE] --stalwart-container NAME --account-id ID --sender ADDRESS [--jmap-url URL --admin-url URL --output FILE]" >&2
  exit 2
}
fail() { echo "Resend certification preflight refused: $1" >&2; exit 1; }

[ -n "$profile" ] && [ -f "$profile" ] || usage
shift
while [ "$#" -gt 0 ]; do
  case "$1" in
    --secret-file) [ "$#" -ge 2 ] || usage; secret_file="$2"; shift 2 ;;
    --relay-secret-file) [ "$#" -ge 2 ] || usage; relay_secret_file="$2"; shift 2 ;;
    --stalwart-container) [ "$#" -ge 2 ] || usage; container="$2"; shift 2 ;;
    --account-id) [ "$#" -ge 2 ] || usage; account_id="$2"; shift 2 ;;
    --sender) [ "$#" -ge 2 ] || usage; sender="$2"; shift 2 ;;
    --jmap-url) [ "$#" -ge 2 ] || usage; jmap_url="$2"; shift 2 ;;
    --admin-url) [ "$#" -ge 2 ] || usage; admin_url="$2"; shift 2 ;;
    --output) [ "$#" -ge 2 ] || usage; output="$2"; shift 2 ;;
    *) usage ;;
  esac
done

env_value() { awk -F= -v key="$1" '$1 == key {sub(/^[^=]*=/, ""); sub(/\r$/, ""); print; exit}' "$profile"; }
secret_value() { awk -F= -v key="$1" '$1 == key {sub(/^[^=]*=/, ""); sub(/\r$/, ""); print; exit}' "$secret_file"; }
relay_secret_value() { awk -F= -v key="$1" '$1 == key {sub(/^[^=]*=/, ""); sub(/\r$/, ""); print; exit}' "$relay_secret_file"; }
require_value() { test "$(env_value "$1")" = "$2" || fail "$1 must be $2"; }
require_command() { command -v "$1" >/dev/null 2>&1 || fail "$1 is required"; }

require_value DEPLOYMENT_TOPOLOGY smtp_gateway_vps_home_stalwart_resend
require_value AGENT_MAIL_DOMAIN agents.aiat.ca
require_value DEFAULT_OUTBOUND_ENABLED false
require_value OUTBOUND_RELAY_CERTIFIED false
require_value DIRECT_MX_OUTBOUND_ENABLED false
require_value OUTBOUND_RELAY_HOST smtp.resend.com
require_value OUTBOUND_RELAY_PORT 465
require_value OUTBOUND_RELAY_TLS_MODE implicit

[ -n "$secret_file" ] && [ -f "$secret_file" ] || fail "protected secret file is required"
relay_secret_file="${relay_secret_file:-$secret_file}"
[ -f "$relay_secret_file" ] || fail "protected relay secret file is required"
[ -n "$container" ] || fail "--stalwart-container is required"
[ -n "$account_id" ] || fail "--account-id is required"
printf '%s\n' "$sender" | grep -Eq '^[^[:space:]@]+@agents\.aiat\.ca$' || fail "--sender must be an agents.aiat.ca address"
test "$jmap_url" = http://127.0.0.1:18080 || fail "JMAP must remain local at http://127.0.0.1:18080"
test "$admin_url" = http://127.0.0.1:18080/api || fail "admin JMAP must remain local at http://127.0.0.1:18080/api"

for command in awk curl jq docker grep sha256sum ss stat mktemp nc openssl timeout; do require_command "$command"; done
uid="$(id -u)"
for protected_file in "$secret_file" "$relay_secret_file"; do
  mode="$(stat -c '%a' "$protected_file" 2>/dev/null || true)"
  case "$mode" in 400|600) ;; *) fail "secret files must have mode 0400 or 0600" ;; esac
  owner="$(stat -c '%u' "$protected_file" 2>/dev/null || true)"
  test "$owner" = 0 || test "$owner" = "$uid" || fail "secret file owner must be root or current user"
done

resend_api_key="$(relay_secret_value RESEND_API_KEY)"
stalwart_api_key="$(secret_value STALWART_API_KEY)"
service_token="$(secret_value STALWART_JMAP_SERVICE_TOKEN)"
[ "${#resend_api_key}" -ge 20 ] || fail "RESEND_API_KEY is missing or too short"
[ -n "$stalwart_api_key" ] || fail "STALWART_API_KEY is missing"
[ -n "$service_token" ] || fail "STALWART_JMAP_SERVICE_TOKEN is missing"

docker inspect -f '{{.State.Running}}' "$container" 2>/dev/null | grep -Fx true >/dev/null || fail "Stalwart container is not running"
ss -ltn | grep -Eq '127\.0\.0\.1:18080' || fail "Stalwart HTTP/admin/JMAP is not bound to 127.0.0.1:18080"

# This only establishes transport and certificate readiness.  Authentication,
# provider acceptance, and external delivery are proven later by the one
# approved Stalwart/JMAP submission and its completion evidence.
nc -z -w 15 smtp.resend.com 465 >/dev/null 2>&1 || fail "smtp.resend.com:465 TCP reachability failed"
tls_output="$(timeout 20 openssl s_client -connect smtp.resend.com:465 -servername smtp.resend.com -verify_return_error -brief </dev/null 2>&1 || true)"
printf '%s\n' "$tls_output" | grep -Eq 'Verification:[[:space:]]+OK|Verify return code:[[:space:]]+0[[:space:]]+\(ok\)' || \
  fail "smtp.resend.com:465 implicit-TLS certificate verification failed"

local_fingerprint="$(printf %s "$resend_api_key" | sha256sum | awk '{print $1}')"
container_fingerprint_command='test -n "$RESEND_API_KEY" || exit 11; command -v sha256sum >/dev/null 2>&1 || exit 12; printf %s "$RESEND_API_KEY" | sha256sum | awk "{print \$1}"'
container_fingerprint="$(docker exec "$container" sh -c "$container_fingerprint_command" 2>/dev/null || true)"
test -n "$container_fingerprint" && test "$container_fingerprint" = "$local_fingerprint" || fail "protected RESEND_API_KEY does not match the running Stalwart secret source"

curl_config="$(mktemp)"
payload_file="$(mktemp)"
tmp_output=""
umask 077
cleanup() {
  rm -f "$curl_config" "$payload_file"
  if [ -n "$tmp_output" ]; then rm -f "$tmp_output"; fi
  unset resend_api_key stalwart_api_key service_token local_fingerprint container_fingerprint
}
trap cleanup EXIT INT TERM
chmod 600 "$curl_config" "$payload_file"
case "$service_token" in Bearer\ *|Basic\ *|OAuth\ *) auth_header="$service_token" ;; *) auth_header="Bearer $service_token" ;; esac
printf 'url = "http://127.0.0.1:18080/jmap"\nrequest = POST\nheader = "Authorization: %s"\nheader = "Content-Type: application/json"\n' "$auth_header" >"$curl_config"
jmap() { printf '%s' "$1" >"$payload_file"; curl --silent --show-error --fail --config "$curl_config" --data-binary "@$payload_file"; }

mail_payload="$(jq -cn --arg account "$account_id" '{using:["urn:ietf:params:jmap:core","urn:ietf:params:jmap:mail"],methodCalls:[["Mailbox/get",{accountId:$account},"mailboxes"],["Identity/get",{accountId:$account},"identities"]]}')"
mail_response="$(jmap "$mail_payload")" || fail "local JMAP service credential was rejected"
printf '%s' "$mail_response" | jq -e --arg sender "$sender" '
  any(.methodResponses[]?; .[0] == "Mailbox/get" and ((.[1].list // []) | length > 0)) and
  any(.methodResponses[]?; .[0] == "Identity/get" and any((.[1].list // [])[]?; (.email // "") | ascii_downcase == ($sender | ascii_downcase)))
' >/dev/null || fail "production sender account does not exist in the local Stalwart instance"

if ! STALWART_ADMIN_URL="$admin_url" STALWART_API_KEY="$stalwart_api_key" STALWART_ADMIN_INSECURE_TLS=false \
  sh "$base_dir/../../mail-edge/scripts/verify-stalwart-relay.sh" >/dev/null 2>&1; then
  fail "management credential or exact environment-backed resend-relay configuration is not ready"
fi

if [ -n "$output" ]; then
  tmp_output="${output}.tmp.$$"
  {
    echo "RESEND_CERTIFICATION_PREFLIGHT=PASS"
    echo "CERTIFICATION_SCOPE=local_wsl_loopback"
    echo "JMAP_URL=http://127.0.0.1:18080"
    echo "ADMIN_URL=http://127.0.0.1:18080/api"
    echo "STALWART_CONTAINER=$container"
    echo "PRODUCTION_SENDER=$sender"
    echo "JMAP_SERVICE_CREDENTIAL=PASS"
    echo "MANAGEMENT_CREDENTIAL=PASS"
    echo "RELAY_SECRET_SOURCE_MATCH=PASS"
    echo "STALWART_ROUTE=resend-relay"
    echo "DIRECT_MX_OUTBOUND_ENABLED=false"
  } >"$tmp_output"
  mv -n "$tmp_output" "$output" 2>/dev/null || fail "refusing to overwrite preflight output"
  tmp_output=""
else
  echo "Resend certification preflight passed for local WSL Stalwart; no message or configuration was changed."
fi
