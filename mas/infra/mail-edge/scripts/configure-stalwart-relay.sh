#!/bin/sh
# Apply and verify the only permitted Stalwart remote-delivery route.
set -eu

: "${STALWART_ADMIN_URL:=https://stalwart:443/api}"
: "${STALWART_API_KEY:?Stalwart API key is required}"
: "${STALWART_ADMIN_INSECURE_TLS:=true}"

base_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
policy="${STALWART_RELAY_POLICY:-$base_dir/../stalwart-relay-policy.json}"
command -v curl >/dev/null 2>&1 || { echo "curl is required" >&2; exit 1; }
command -v jq >/dev/null 2>&1 || { echo "jq is required" >&2; exit 1; }
test -r "$policy"
jq -e '.mta_route["@type"] == "Relay"' "$policy" >/dev/null

curl_args="-fsS"
if [ "$STALWART_ADMIN_INSECURE_TLS" = "true" ]; then curl_args="$curl_args -k"; fi
jmap() {
  # shellcheck disable=SC2086
  curl $curl_args -X POST "$STALWART_ADMIN_URL" -H "Authorization: Bearer $STALWART_API_KEY" -H 'Content-Type: application/json' --data-binary "$1"
}

routes_payload='{"using":["urn:ietf:params:jmap:core","urn:stalwart:jmap"],"methodCalls":[["x:MtaRoute/get",{},"routes"]]}'
routes="$(jmap "$routes_payload")"
# Reconcile every remote-delivery route, not just the conventional `mx` name.
# Leaving an old Relay route behind would violate the Resend-only boundary even
# if the active strategy did not currently select it.
for route_id in $(printf '%s' "$routes" | jq -r '.methodResponses[0][1].list[]? | select(."@type" == "Mx" or ."@type" == "Relay") | .id'); do
  delete_payload="$(jq -cn --arg id "$route_id" '{using:["urn:ietf:params:jmap:core","urn:stalwart:jmap"],methodCalls:[["x:MtaRoute/set",{destroy:[$id]},"delete"]]}')"
  jmap "$delete_payload" >/dev/null
done

route_json="$(jq -c '.mta_route' "$policy")"
strategy_json="$(jq -c '.mta_outbound_strategy_patch' "$policy")"
create_payload="$(jq -cn --argjson route "$route_json" '{using:["urn:ietf:params:jmap:core","urn:stalwart:jmap"],methodCalls:[["x:MtaRoute/set",{create:{"resend-relay":$route}},"create"]]}')"
strategy_payload="$(jq -cn --argjson patch "$strategy_json" '{using:["urn:ietf:params:jmap:core","urn:stalwart:jmap"],methodCalls:[["x:MtaOutboundStrategy/set",{update:{singleton:$patch}},"strategy"]]}')"
jmap "$create_payload" >/dev/null
jmap "$strategy_payload" >/dev/null
"${STALWART_RELAY_VERIFY_SCRIPT:-$base_dir/verify-stalwart-relay.sh}"
