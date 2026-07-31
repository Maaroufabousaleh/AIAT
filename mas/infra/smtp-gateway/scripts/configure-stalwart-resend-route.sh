#!/bin/sh
# Explicit local-only route lifecycle management. It never recreates a
# container, account, mailbox, message store, or volume.
set -eu

base_dir="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
action="${1:-}"
profile="${2:-}"
secret_file=""
relay_secret_file=""
container=""
backup=""
admin_url="http://127.0.0.1:18080"
policy="$base_dir/../mail-edge/stalwart-relay-policy.json"
jmap_helper="$base_dir/scripts/stalwart_jmap_endpoint.py"

usage() {
  echo "usage: $0 backup|apply|verify|rollback PROFILE --secret-file FILE [--relay-secret-file FILE] --stalwart-container NAME --backup FILE [--admin-url http://127.0.0.1:18080 --policy FILE]" >&2
  exit 2
}
fail() { echo "Stalwart Resend route $action refused: $1" >&2; exit 1; }

case "$action" in backup|apply|verify|rollback) ;; *) usage ;; esac
[ -f "$profile" ] || usage
shift 2
while [ "$#" -gt 0 ]; do
  case "$1" in
    --secret-file) [ "$#" -ge 2 ] || usage; secret_file="$2"; shift 2 ;;
    --relay-secret-file) [ "$#" -ge 2 ] || usage; relay_secret_file="$2"; shift 2 ;;
    --stalwart-container) [ "$#" -ge 2 ] || usage; container="$2"; shift 2 ;;
    --backup) [ "$#" -ge 2 ] || usage; backup="$2"; shift 2 ;;
    --admin-url) [ "$#" -ge 2 ] || usage; admin_url="$2"; shift 2 ;;
    --policy) [ "$#" -ge 2 ] || usage; policy="$2"; shift 2 ;;
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
require_value DIRECT_MX_OUTBOUND_ENABLED false
require_value DEFAULT_OUTBOUND_ENABLED false
require_value OUTBOUND_RELAY_CERTIFIED false
require_value OUTBOUND_RELAY_HOST smtp.resend.com
require_value OUTBOUND_RELAY_PORT 465
require_value OUTBOUND_RELAY_TLS_MODE implicit
[ -n "$secret_file" ] && [ -f "$secret_file" ] || fail "protected secret file is required"
relay_secret_file="${relay_secret_file:-$secret_file}"
[ -f "$relay_secret_file" ] || fail "protected relay secret file is required"
[ -n "$container" ] || fail "--stalwart-container is required"
[ -n "$backup" ] || fail "--backup is required"
require_command python3
python3 "$jmap_helper" --base-url "$admin_url" --validate-only >/dev/null 2>&1 || \
  fail "admin URL must be an HTTP loopback Stalwart base or session URL"
for command in awk curl jq docker grep sha256sum stat mktemp; do require_command "$command"; done

uid="$(id -u)"
for protected_file in "$secret_file" "$relay_secret_file"; do
  mode="$(stat -c '%a' "$protected_file" 2>/dev/null || true)"
  case "$mode" in 400|600) ;; *) fail "secret files must have mode 0400 or 0600" ;; esac
  owner="$(stat -c '%u' "$protected_file" 2>/dev/null || true)"
  test "$owner" = 0 || test "$owner" = "$uid" || fail "secret file owner must be root or current user"
done
resend_api_key="$(relay_secret_value RESEND_API_KEY)"
stalwart_api_key="$(secret_value STALWART_API_KEY)"
[ "${#resend_api_key}" -ge 20 ] || fail "RESEND_API_KEY is missing or too short"
[ -n "$stalwart_api_key" ] || fail "STALWART_API_KEY is missing"

docker inspect -f '{{.State.Running}}' "$container" 2>/dev/null | grep -Fx true >/dev/null || fail "Stalwart container is not running"
local_fingerprint="$(printf %s "$resend_api_key" | sha256sum | awk '{print $1}')"
container_fingerprint_command='test -n "$RESEND_API_KEY" || exit 11; command -v sha256sum >/dev/null 2>&1 || exit 12; printf %s "$RESEND_API_KEY" | sha256sum | awk "{print \$1}"'
container_fingerprint="$(docker exec "$container" sh -c "$container_fingerprint_command" 2>/dev/null || true)"
test -n "$container_fingerprint" && test "$container_fingerprint" = "$local_fingerprint" || fail "protected RESEND_API_KEY does not match the running Stalwart secret source"

curl_config="$(mktemp)"
payload_file="$(mktemp)"
tmp_backup=""
umask 077
cleanup() {
  rm -f "$curl_config" "$payload_file"
  if [ -n "$tmp_backup" ]; then rm -f "$tmp_backup"; fi
  unset resend_api_key stalwart_api_key local_fingerprint container_fingerprint
}
trap cleanup EXIT INT TERM
chmod 600 "$curl_config" "$payload_file"
if ! jmap_url="$(STALWART_JMAP_AUTHORIZATION="Bearer $stalwart_api_key" python3 "$jmap_helper" --base-url "$admin_url")"; then
  fail "could not discover the local Stalwart JMAP endpoint"
fi
printf 'url = "%s"\nrequest = POST\nheader = "Authorization: Bearer %s"\nheader = "Content-Type: application/json"\n' "$jmap_url" "$stalwart_api_key" >"$curl_config"
jmap() { printf '%s' "$1" >"$payload_file"; curl --silent --show-error --fail --config "$curl_config" --data-binary "@$payload_file"; }
routes_payload='{"using":["urn:ietf:params:jmap:core","urn:stalwart:jmap"],"methodCalls":[["x:MtaRoute/get",{},"routes"]]}'
strategy_payload='{"using":["urn:ietf:params:jmap:core","urn:stalwart:jmap"],"methodCalls":[["x:MtaOutboundStrategy/get",{"ids":["singleton"]},"strategy"]]}'
remote_ids() { printf '%s' "$1" | jq -c '[.methodResponses[0][1].list[]? | select(."@type" == "Mx" or ."@type" == "Relay") | .id]'; }
destroy_remote_routes() {
  current_routes="$(jmap "$routes_payload")" || fail "could not read current routes"
  ids="$(remote_ids "$current_routes")"
  test "$ids" = '[]' || jmap "$(jq -cn --argjson ids "$ids" '{using:["urn:ietf:params:jmap:core","urn:stalwart:jmap"],methodCalls:[["x:MtaRoute/set",{destroy:$ids},"destroy-remote"]]}')" >/dev/null
}
verify_route() {
  STALWART_ADMIN_URL="$admin_url" STALWART_API_KEY="$stalwart_api_key" STALWART_ADMIN_INSECURE_TLS=false \
    sh "$base_dir/../mail-edge/scripts/verify-stalwart-relay.sh" >/dev/null 2>&1 || fail "exact environment-backed resend-relay verification failed"
}

case "$action" in
  backup)
    test ! -e "$backup" || fail "refusing to overwrite existing backup"
    routes="$(jmap "$routes_payload")" || fail "could not back up routes"
    strategy="$(jmap "$strategy_payload")" || fail "could not back up outbound strategy"
    tmp_backup="${backup}.tmp.$$"
    jq -cn --argjson routes "$routes" --argjson strategy "$strategy" '{version:1,scope:"stalwart-remote-route-and-strategy",routes:$routes,strategy:$strategy}' >"$tmp_backup"
    chmod 600 "$tmp_backup"
    mv "$tmp_backup" "$backup"
    tmp_backup=""
    echo "Stalwart remote route/strategy backup written; no live configuration changed."
    ;;
  apply)
    test -s "$backup" || fail "a prior route/strategy backup is required before apply"
    jq -e '.version == 1 and .routes and .strategy' "$backup" >/dev/null || fail "backup format is invalid"
    test -f "$policy" || fail "relay policy is missing"
    jq -e '.mta_route.authUsername == "resend" and .mta_route.authSecret == {"@type":"EnvironmentVariable","variableName":"RESEND_API_KEY"}' "$policy" >/dev/null || fail "relay policy is not environment-backed"
    destroy_remote_routes
    route="$(jq -c '.mta_route' "$policy")"
    patch="$(jq -c '.mta_outbound_strategy_patch' "$policy")"
    jmap "$(jq -cn --argjson route "$route" '{using:["urn:ietf:params:jmap:core","urn:stalwart:jmap"],methodCalls:[["x:MtaRoute/set",{create:{"resend-relay":$route}},"create-relay"]]}')" >/dev/null || fail "could not create resend-relay"
    jmap "$(jq -cn --argjson patch "$patch" '{using:["urn:ietf:params:jmap:core","urn:stalwart:jmap"],methodCalls:[["x:MtaOutboundStrategy/set",{update:{singleton:$patch}},"set-strategy"]]}')" >/dev/null || fail "could not select resend-relay"
    verify_route
    echo "Resend-only route applied after backup; accounts, mailboxes, messages, container, and volumes were not recreated."
    ;;
  verify)
    verify_route
    echo "Local Stalwart Resend-only route verifies; no configuration changed."
    ;;
  rollback)
    test -s "$backup" || fail "route/strategy backup is required for rollback"
    jq -e '.version == 1 and .routes and .strategy' "$backup" >/dev/null || fail "backup format is invalid"
    destroy_remote_routes
    restore_routes="$(jq -c '[.routes.methodResponses[0][1].list[]? | select(."@type" == "Mx" or ."@type" == "Relay")] | reduce .[] as $route ({}; .[$route.name] = ($route | del(.id, .name)))' "$backup")"
    test "$restore_routes" = '{}' || jmap "$(jq -cn --argjson create "$restore_routes" '{using:["urn:ietf:params:jmap:core","urn:stalwart:jmap"],methodCalls:[["x:MtaRoute/set",{create:$create},"restore-routes"]]}')" >/dev/null || fail "could not restore backed-up remote routes"
    restore_strategy="$(jq -c '.strategy.methodResponses[0][1].list[0] | del(.id)' "$backup")"
    test "$restore_strategy" != null || fail "backup has no outbound strategy"
    jmap "$(jq -cn --argjson patch "$restore_strategy" '{using:["urn:ietf:params:jmap:core","urn:stalwart:jmap"],methodCalls:[["x:MtaOutboundStrategy/set",{update:{singleton:$patch}},"restore-strategy"]]}')" >/dev/null || fail "could not restore outbound strategy"
    echo "Backed-up remote route/strategy restored; accounts, mailboxes, messages, container, and volumes were not recreated."
    ;;
esac
