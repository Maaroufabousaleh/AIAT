#!/bin/sh
# Read-only verification of the live Stalwart route and strategy objects.
set -eu

: "${STALWART_ADMIN_URL:=http://127.0.0.1:18080}"
: "${STALWART_API_KEY:?Stalwart API key is required}"
: "${STALWART_ADMIN_INSECURE_TLS:=false}"
action="verify"
base_dir="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
jmap_helper="$base_dir/../smtp-gateway/scripts/stalwart_jmap_endpoint.py"
response_validator="$base_dir/../smtp-gateway/scripts/stalwart_jmap_response.py"
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
request_file=""
response_file=""
umask 077
cleanup() {
  rm -f "$curl_config" "$request_file" "$response_file"
  unset jmap_url
}
trap cleanup EXIT INT TERM
curl_config="$(mktemp)"
request_file="$(mktemp)"
response_file="$(mktemp)"
chmod 600 "$curl_config"
printf 'url = "%s"\nrequest = POST\nheader = "Authorization: Bearer %s"\nheader = "Content-Type: application/json"\n' "$jmap_url" "$STALWART_API_KEY" >"$curl_config"
jmap() {
  printf '%s' "$1" >"$request_file"
  : >"$response_file"
  # shellcheck disable=SC2086
  http_status="$(curl $curl_args --config "$curl_config" --data-binary "@$request_file" \
    --output "$response_file" --write-out '%{http_code}' 2>/dev/null || true)"
  if [ "$http_status" != 200 ]; then
    python3 "$response_validator" --request-file "$request_file" --action "$action" \
      --http-status "${http_status:-transport-failure}" --endpoint-path "/jmap/" \
      <"$response_file" >&2 || true
    return 1
  fi
  python3 "$response_validator" --request-file "$request_file" --action "$action" \
    --http-status "$http_status" --endpoint-path "/jmap/" <"$response_file" || return 1
  cat "$response_file"
}

routes_payload='{"using":["urn:ietf:params:jmap:core","urn:stalwart:jmap"],"methodCalls":[["x:MtaRoute/get",{},"routes"]]}'
strategy_payload='{"using":["urn:ietf:params:jmap:core","urn:stalwart:jmap"],"methodCalls":[["x:MtaOutboundStrategy/get",{"ids":["singleton"]},"strategy"]]}'
routes="$(jmap "$routes_payload")"
strategy="$(jmap "$strategy_payload")"
printf '%s' "$routes" | jq -e '[.methodResponses[0][1].list[]? | select(."@type" == "Mx" or (."@type" == "Relay" and .name != "resend-relay"))] | length == 0' >/dev/null || { echo "an unapproved remote-delivery route remains configured" >&2; exit 1; }
printf '%s' "$routes" | jq -e '[.methodResponses[0][1].list[]? | select(.name == "resend-relay" and ."@type" == "Relay" and .address == "smtp.resend.com" and .port == 465 and .protocol == "smtp" and .implicitTls == true and .allowInvalidCerts == false and .authUsername == "resend" and .authSecret == {"@type":"EnvironmentVariable","variableName":"RESEND_API_KEY"})] | length == 1' >/dev/null || { echo "exactly one safe environment-backed Resend relay route is required" >&2; exit 1; }
expected_route="'resend-relay'"
printf '%s' "$strategy" | jq -e --arg expected "$expected_route" '
  .methodResponses[0][1].list | length == 1 and
  .[0].route == {"match":{"0":{"if":"is_local_domain(rcpt_domain)","then":"\u0027local\u0027"}},"else":$expected}
' >/dev/null || { echo "remote strategy does not select resend-relay" >&2; exit 1; }
echo "Stalwart remote delivery is relay-only and direct MX is disabled."
