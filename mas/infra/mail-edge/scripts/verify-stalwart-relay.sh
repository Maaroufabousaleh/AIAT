#!/bin/sh
# Read-only verification of the live Stalwart route and strategy objects.
set -eu

: "${STALWART_ADMIN_URL:=http://127.0.0.1:18080}"
: "${STALWART_API_KEY:?Stalwart API key is required}"
: "${STALWART_ADMIN_INSECURE_TLS:=false}"
base_dir="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
jmap_helper="$base_dir/../smtp-gateway/scripts/stalwart_jmap_endpoint.py"
command -v curl >/dev/null 2>&1 || { echo "curl is required" >&2; exit 1; }
command -v jq >/dev/null 2>&1 || { echo "jq is required" >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "python3 is required" >&2; exit 1; }
command -v mktemp >/dev/null 2>&1 || { echo "mktemp is required" >&2; exit 1; }
python3 "$jmap_helper" --base-url "$STALWART_ADMIN_URL" --validate-only >/dev/null 2>&1 || {
  echo "Stalwart admin URL must be an HTTP loopback base or session URL" >&2
  exit 1
}
if ! jmap_url="$(STALWART_JMAP_AUTHORIZATION="Bearer $STALWART_API_KEY" python3 "$jmap_helper" --base-url "$STALWART_ADMIN_URL")"; then
  echo "Stalwart local JMAP endpoint discovery failed" >&2
  exit 1
fi
curl_args="-fsS"
if [ "$STALWART_ADMIN_INSECURE_TLS" = "true" ]; then curl_args="$curl_args -k"; fi
curl_config=""
umask 077
cleanup() {
  if [ -n "$curl_config" ]; then rm -f "$curl_config"; fi
  unset jmap_url
}
trap cleanup EXIT INT TERM
curl_config="$(mktemp)"
chmod 600 "$curl_config"
printf 'url = "%s"\nrequest = POST\nheader = "Authorization: Bearer %s"\nheader = "Content-Type: application/json"\n' "$jmap_url" "$STALWART_API_KEY" >"$curl_config"
jmap() {
  # shellcheck disable=SC2086
  curl $curl_args --config "$curl_config" --data-binary "$1"
}

routes_payload='{"using":["urn:ietf:params:jmap:core","urn:stalwart:jmap"],"methodCalls":[["x:MtaRoute/get",{},"routes"]]}'
strategy_payload='{"using":["urn:ietf:params:jmap:core","urn:stalwart:jmap"],"methodCalls":[["x:MtaOutboundStrategy/get",{"ids":["singleton"]},"strategy"]]}'
routes="$(jmap "$routes_payload")"
strategy="$(jmap "$strategy_payload")"
printf '%s' "$routes" | jq -e '[.methodResponses[0][1].list[]? | select(."@type" == "Mx" or (."@type" == "Relay" and .name != "resend-relay"))] | length == 0' >/dev/null || { echo "an unapproved remote-delivery route remains configured" >&2; exit 1; }
printf '%s' "$routes" | jq -e '[.methodResponses[0][1].list[]? | select(.name == "resend-relay" and ."@type" == "Relay" and .address == "smtp.resend.com" and .port == 465 and .implicitTls == true and .allowInvalidCerts == false and .authUsername == "resend" and .authSecret == {"@type":"EnvironmentVariable","variableName":"RESEND_API_KEY"})] | length == 1' >/dev/null || { echo "exactly one safe environment-backed Resend relay route is required" >&2; exit 1; }
expected_route="'resend-relay'"
printf '%s' "$strategy" | jq -e --arg expected "$expected_route" '.methodResponses[0][1].list[0].route.else == $expected' >/dev/null || { echo "remote strategy does not select resend-relay" >&2; exit 1; }
echo "Stalwart remote delivery is relay-only and direct MX is disabled."
