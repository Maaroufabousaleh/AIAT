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
response_validator="$base_dir/scripts/stalwart_jmap_response.py"

usage() {
  echo "usage: $0 backup|apply|verify|rollback|inspect PROFILE --secret-file FILE [--relay-secret-file FILE] --stalwart-container NAME --backup FILE [--admin-url http://127.0.0.1:18080 --policy FILE]" >&2
  exit 2
}
fail() { echo "Stalwart Resend route $action refused: $1" >&2; exit 1; }

case "$action" in backup|apply|verify|rollback|inspect) ;; *) usage ;; esac
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

umask 077
curl_config="$(mktemp)"
payload_file="$(mktemp)"
tmp_backup=""
response_file=""
JMAP_FAILURE_UNCERTAIN=0
cleanup() {
  rm -f "$curl_config" "$payload_file"
  if [ -n "$tmp_backup" ]; then rm -f "$tmp_backup"; fi
  if [ -n "$response_file" ]; then rm -f "$response_file"; fi
  unset resend_api_key stalwart_api_key local_fingerprint container_fingerprint
}
trap cleanup EXIT INT TERM
chmod 600 "$curl_config" "$payload_file"
if ! jmap_url="$(STALWART_JMAP_AUTHORIZATION="Bearer $stalwart_api_key" python3 "$jmap_helper" --base-url "$admin_url")"; then
  fail "could not discover the local Stalwart JMAP endpoint"
fi
printf 'url = "%s"\nrequest = POST\nheader = "Authorization: Bearer %s"\nheader = "Content-Type: application/json"\n' "$jmap_url" "$stalwart_api_key" >"$curl_config"
jmap() {
  printf '%s' "$1" >"$payload_file"
  response_file="$(mktemp)"
  JMAP_FAILURE_UNCERTAIN=0
  http_status="$(curl --silent --show-error --config "$curl_config" --data-binary "@$payload_file" \
    --output "$response_file" --write-out '%{http_code}' 2>/dev/null || true)"
  if [ "$http_status" != 200 ]; then
    JMAP_FAILURE_UNCERTAIN=1
    python3 "$response_validator" --request-file "$payload_file" --action "$action" \
      --http-status "${http_status:-transport-failure}" --endpoint-path "/jmap/" \
      <"$response_file" >&2 || true
    rm -f "$response_file"
    response_file=""
    return 1
  fi
  if ! python3 "$response_validator" --request-file "$payload_file" --action "$action" \
    --http-status "$http_status" --endpoint-path "/jmap/" <"$response_file"; then
    rm -f "$response_file"
    response_file=""
    return 1
  fi
  cat "$response_file"
  rm -f "$response_file"
  response_file=""
}
routes_payload='{"using":["urn:ietf:params:jmap:core","urn:stalwart:jmap"],"methodCalls":[["x:MtaRoute/get",{},"routes"]]}'
strategy_payload='{"using":["urn:ietf:params:jmap:core","urn:stalwart:jmap"],"methodCalls":[["x:MtaOutboundStrategy/get",{"ids":["singleton"]},"strategy"]]}'
remote_ids() { printf '%s' "$1" | jq -c '[.methodResponses[0][1].list[]? | select(."@type" == "Mx" or ."@type" == "Relay") | .id]'; }
destroy_remote_routes() {
  current_routes="${1:-}"
  [ -n "$current_routes" ] || current_routes="$(jmap "$routes_payload")" || return 1
  ids="$(remote_ids "$current_routes")"
  if [ "$ids" != '[]' ]; then
    jmap "$(jq -cn --argjson ids "$ids" '{using:["urn:ietf:params:jmap:core","urn:stalwart:jmap"],methodCalls:[["x:MtaRoute/set",{destroy:$ids},"destroy-remote"]]}')" >/dev/null || return 1
    mutation_succeeded=1
  fi
}
verify_route() {
  STALWART_ADMIN_URL="$admin_url" STALWART_API_KEY="$stalwart_api_key" STALWART_ADMIN_INSECURE_TLS=false \
    sh "$base_dir/../mail-edge/scripts/verify-stalwart-relay.sh" >/dev/null || return 1
}

validate_policy() {
  test -f "$policy" || return 1
  jq -e '
    .version == 1 and
    .mta_route["@type"] == "Relay" and
    .mta_route.name == "resend-relay" and
    .mta_route.address == "smtp.resend.com" and
    .mta_route.port == 465 and
    .mta_route.protocol == "smtp" and
    .mta_route.implicitTls == true and
    .mta_route.allowInvalidCerts == false and
    .mta_route.authUsername == "resend" and
    .mta_route.authSecret == {"@type":"EnvironmentVariable","variableName":"RESEND_API_KEY"} and
    .mta_outbound_strategy_patch.route == {
      "match":{"0":{"if":"is_local_domain(rcpt_domain)","then":"\u0027local\u0027"}},
      "else":"\u0027resend-relay\u0027"
    }
  ' "$policy" >/dev/null
}

secret_safe_routes() {
  printf '%s' "$1" | jq -e '
    [.methodResponses[0][1].list[]? | select(has("authSecret")) | .authSecret]
    | all(.[]; . == {"@type":"EnvironmentVariable","variableName":"RESEND_API_KEY"})
  ' >/dev/null
}

validate_backup() {
  test -s "$backup" || return 1
  jq -e '.version == 1 and .scope == "stalwart-remote-route-and-strategy" and .routes and .strategy' "$backup" >/dev/null || return 1
  secret_safe_routes "$(jq -c '.routes' "$backup")" || return 1
  printf '%s' "$routes_payload" >"$payload_file"
  jq -c '.routes' "$backup" | python3 "$response_validator" --request-file "$payload_file" \
    --action "${action}-backup" --http-status 200 --endpoint-path "/jmap/" >/dev/null || return 1
  printf '%s' "$strategy_payload" >"$payload_file"
  jq -c '.strategy' "$backup" | python3 "$response_validator" --request-file "$payload_file" \
    --action "${action}-backup" --http-status 200 --endpoint-path "/jmap/" >/dev/null || return 1
}

state_matches_backup() {
  routes_a="$1"
  strategy_a="$2"
  routes_b="$3"
  strategy_b="$4"
  test "$(printf '%s' "$routes_a" | jq -cS '[.methodResponses[0][1].list[]? | del(.id)] | sort_by(.name)')" = \
    "$(printf '%s' "$routes_b" | jq -cS '[.methodResponses[0][1].list[]? | del(.id)] | sort_by(.name)')" || return 1
  test "$(printf '%s' "$strategy_a" | jq -cS '.methodResponses[0][1].list[0] | del(.id)')" = \
    "$(printf '%s' "$strategy_b" | jq -cS '.methodResponses[0][1].list[0] | del(.id)')"
}

desired_state() {
  printf '%s' "$1" | jq -e '
    ([.methodResponses[0][1].list[]? | select(."@type" == "Mx")] | length == 0) and
    ([.methodResponses[0][1].list[]? | select(."@type" == "Local" and .name == "local")] | length == 1) and
    ([.methodResponses[0][1].list[]? | select(."@type" == "Relay")] | length == 1) and
    ([.methodResponses[0][1].list[]? | select(
      ."@type" == "Relay" and .name == "resend-relay" and
      .address == "smtp.resend.com" and .port == 465 and .protocol == "smtp" and
      .implicitTls == true and .allowInvalidCerts == false and .authUsername == "resend" and
      .authSecret == {"@type":"EnvironmentVariable","variableName":"RESEND_API_KEY"}
    )] | length == 1)
  ' >/dev/null || return 1
  printf '%s' "$2" | jq -e '
    (.methodResponses[0][1].list | length == 1) and
    (.methodResponses[0][1].list[0].route == {
      "match":{"0":{"if":"is_local_domain(rcpt_domain)","then":"\u0027local\u0027"}},
      "else":"\u0027resend-relay\u0027"
    })
  ' >/dev/null
}

read_current_state() {
  current_routes="$(jmap "$routes_payload")" || return 1
  current_strategy="$(jmap "$strategy_payload")" || return 1
}

restore_from_backup() {
  read_current_state || return 1
  destroy_remote_routes "$current_routes" || return 1
  restore_routes="$(jq -c '[.routes.methodResponses[0][1].list[]? | select(."@type" == "Mx" or ."@type" == "Relay")] | reduce .[] as $route ({}; .[$route.name] = ($route | del(.id)))' "$backup")" || return 1
  if [ "$restore_routes" != '{}' ]; then
    jmap "$(jq -cn --argjson create "$restore_routes" '{using:["urn:ietf:params:jmap:core","urn:stalwart:jmap"],methodCalls:[["x:MtaRoute/set",{create:$create},"restore-routes"]]}')" >/dev/null || return 1
    mutation_succeeded=1
  fi
  restore_strategy="$(jq -c '.strategy.methodResponses[0][1].list[0] | del(.id)' "$backup")" || return 1
  test "$restore_strategy" != null || return 1
  jmap "$(jq -cn --argjson patch "$restore_strategy" '{using:["urn:ietf:params:jmap:core","urn:stalwart:jmap"],methodCalls:[["x:MtaOutboundStrategy/set",{update:{singleton:$patch}},"restore-strategy"]]}')" >/dev/null || return 1
  mutation_succeeded=1
  read_current_state || return 1
  state_matches_backup "$current_routes" "$current_strategy" "$(jq -c '.routes' "$backup")" "$(jq -c '.strategy' "$backup")"
}

apply_failure() {
  if [ "$mutation_succeeded" -eq 0 ] && [ "$JMAP_FAILURE_UNCERTAIN" -eq 0 ]; then
    echo "APPLY=FAIL" >&2
    echo "AUTOMATIC_ROLLBACK=NOT_NEEDED" >&2
    return 1
  fi
  if restore_from_backup; then
    echo "APPLY=FAIL" >&2
    echo "AUTOMATIC_ROLLBACK=PASS" >&2
  else
    echo "APPLY=FAIL" >&2
    echo "AUTOMATIC_ROLLBACK=FAIL" >&2
    echo "CRITICAL_FAIL_CLOSED=TRUE" >&2
  fi
  return 1
}

apply_action() {
  validate_backup || { fail "backup validation failed"; return 1; }
  validate_policy || { fail "relay policy does not match the Stalwart v0.16.15 schema"; return 1; }
  read_current_state || { fail "could not read current route and strategy state"; return 1; }
  if desired_state "$current_routes" "$current_strategy"; then
    echo "APPLY=PASS"
    echo "IDEMPOTENT=TRUE"
    echo "No route mutation was required; desired Resend state already exists."
    return 0
  fi
  backup_routes="$(jq -c '.routes' "$backup")"
  backup_strategy="$(jq -c '.strategy' "$backup")"
  state_matches_backup "$current_routes" "$current_strategy" "$backup_routes" "$backup_strategy" || {
    fail "current route and strategy state does not match the protected backup or desired retry state"
    return 1
  }
  mutation_succeeded=0
  destroy_remote_routes "$current_routes" || apply_failure
  route="$(jq -c '.mta_route' "$policy")"
  jmap "$(jq -cn --argjson route "$route" '{using:["urn:ietf:params:jmap:core","urn:stalwart:jmap"],methodCalls:[["x:MtaRoute/set",{create:{"resend-relay":$route}},"create-relay"]]}')" >/dev/null || apply_failure
  mutation_succeeded=1
  patch="$(jq -c '.mta_outbound_strategy_patch' "$policy")"
  jmap "$(jq -cn --argjson patch "$patch" '{using:["urn:ietf:params:jmap:core","urn:stalwart:jmap"],methodCalls:[["x:MtaOutboundStrategy/set",{update:{singleton:$patch}},"set-strategy"]]}')" >/dev/null || apply_failure
  mutation_succeeded=1
  read_current_state || apply_failure
  desired_state "$current_routes" "$current_strategy" || apply_failure
  echo "APPLY=PASS"
  echo "AUTOMATIC_ROLLBACK=NOT_NEEDED"
  echo "Resend-only route applied and verified; Local was preserved and no container or volume was changed."
}

inspect_action() {
  read_current_state || { fail "could not inspect current route and strategy state"; return 1; }
  echo "ROUTE_COUNT=$(printf '%s' "$current_routes" | jq '.methodResponses[0][1].list | length')"
  printf '%s' "$current_routes" | jq -r '.methodResponses[0][1].list[]? |
    "ROUTE_NAME=" + (.name // "<missing>") + "\nROUTE_TYPE=" + (."@type" // "<missing>") +
    "\nROUTE_ADDRESS=" + (if ."@type" == "Relay" then (.address // "<missing>") else "NOT_APPLICABLE" end) +
    "\nROUTE_PORT=" + (if ."@type" == "Relay" then ((.port // "<missing>")|tostring) else "NOT_APPLICABLE" end) +
    "\nROUTE_IMPLICIT_TLS=" + (if ."@type" == "Relay" then ((.implicitTls // "<missing>")|tostring) else "NOT_APPLICABLE" end) +
    "\nROUTE_ALLOW_INVALID_CERTS=" + (if ."@type" == "Relay" then ((.allowInvalidCerts // "<missing>")|tostring) else "NOT_APPLICABLE" end) +
    "\nROUTE_AUTH_USERNAME=" + (if ."@type" == "Relay" then (.authUsername // "<missing>") else "NOT_APPLICABLE" end) +
    "\nROUTE_AUTH_SECRET_TYPE=" + (if ."@type" == "Relay" then (.authSecret["@type"] // "<missing>") else "NOT_APPLICABLE" end) +
    "\nROUTE_AUTH_SECRET_VARIABLE=" + (if ."@type" == "Relay" and .authSecret["@type"] == "EnvironmentVariable" then (.authSecret.variableName // "<missing>") else "NOT_APPLICABLE" end)'
  printf '%s' "$current_strategy" | jq -r '.methodResponses[0][1].list[0].route |
    "STRATEGY_ROUTE_ELSE=" + (.else // "<missing>") +
    "\nSTRATEGY_ROUTE_MATCH_0_IF=" + (.match["0"].if // "<missing>") +
    "\nSTRATEGY_ROUTE_MATCH_0_THEN=" + (.match["0"].then // "<missing>")'
}

case "$action" in
  backup)
    test ! -e "$backup" || fail "refusing to overwrite existing backup"
    routes="$(jmap "$routes_payload")" || fail "could not back up routes"
    secret_safe_routes "$routes" || fail "route backup contains an unsupported secret representation"
    strategy="$(jmap "$strategy_payload")" || fail "could not back up outbound strategy"
    tmp_backup="${backup}.tmp.$$"
    jq -cn --argjson routes "$routes" --argjson strategy "$strategy" '{version:1,scope:"stalwart-remote-route-and-strategy",routes:$routes,strategy:$strategy}' >"$tmp_backup"
    chmod 600 "$tmp_backup"
    mv "$tmp_backup" "$backup"
    tmp_backup=""
    echo "Stalwart remote route/strategy backup written; no live configuration changed."
    ;;
  apply)
    apply_action
    ;;
  verify)
    verify_route
    echo "Local Stalwart Resend-only route verifies; no configuration changed."
    ;;
  rollback)
    validate_backup || fail "route/strategy backup validation failed"
    mutation_succeeded=0
    restore_from_backup || fail "rollback could not be validated"
    echo "ROLLBACK=PASS"
    echo "Backed-up remote route/strategy restored and verified; Local was preserved."
    ;;
  inspect)
    inspect_action
    ;;
esac
