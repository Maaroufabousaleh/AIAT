#!/bin/sh
# Read-only verification of the live Stalwart route and strategy objects.
set -eu

: "${STALWART_ADMIN_URL:=http://stalwart:8080/api}"
: "${STALWART_API_KEY:?Stalwart API key is required}"
: "${STALWART_ADMIN_INSECURE_TLS:=false}"
command -v curl >/dev/null 2>&1 || { echo "curl is required" >&2; exit 1; }
command -v jq >/dev/null 2>&1 || { echo "jq is required" >&2; exit 1; }
curl_args="-fsS"
if [ "$STALWART_ADMIN_INSECURE_TLS" = "true" ]; then curl_args="$curl_args -k"; fi
jmap() {
  # shellcheck disable=SC2086
  curl $curl_args -X POST "$STALWART_ADMIN_URL" -H "Authorization: Bearer $STALWART_API_KEY" -H 'Content-Type: application/json' --data-binary "$1"
}

routes_payload='{"using":["urn:ietf:params:jmap:core","urn:stalwart:jmap"],"methodCalls":[["x:MtaRoute/get",{},"routes"]]}'
strategy_payload='{"using":["urn:ietf:params:jmap:core","urn:stalwart:jmap"],"methodCalls":[["x:MtaOutboundStrategy/get",{"ids":["singleton"]},"strategy"]]}'
routes="$(jmap "$routes_payload")"
strategy="$(jmap "$strategy_payload")"
printf '%s' "$routes" | jq -e '[.methodResponses[0][1].list[]? | select(."@type" == "Mx" or (."@type" == "Relay" and .name != "resend-relay"))] | length == 0' >/dev/null || { echo "an unapproved remote-delivery route remains configured" >&2; exit 1; }
printf '%s' "$routes" | jq -e '[.methodResponses[0][1].list[]? | select(.name == "resend-relay" and ."@type" == "Relay" and .address == "smtp.resend.com" and .port == 465 and .implicitTls == true and .allowInvalidCerts == false)] | length == 1' >/dev/null || { echo "exactly one safe Resend relay route is required" >&2; exit 1; }
expected_route="'resend-relay'"
printf '%s' "$strategy" | jq -e --arg expected "$expected_route" '.methodResponses[0][1].list[0].route.else == $expected' >/dev/null || { echo "remote strategy does not select resend-relay" >&2; exit 1; }
echo "Stalwart remote delivery is relay-only and direct MX is disabled."
