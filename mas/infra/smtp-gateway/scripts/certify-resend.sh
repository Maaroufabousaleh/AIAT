#!/bin/sh
# Send one explicitly approved Stalwart/JMAP certification message.
#
# This script never sends through the Resend API. Stalwart owns the outbound
# SMTP submission and uses its configured environment-backed Resend secret.
# The RESEND_API_KEY is read only to prove that the operator supplied the
# protected relay secret; it is never placed in an argument, output, or
# evidence file.
set -eu

base_dir="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
config_file="${1:-}"
secret_file=""
resend_key_stdin=0
operator_approval=0
jmap_url=""
admin_url=""
account_id=""
sender=""
external_recipient=""
sanitized_output=""
subject=""

usage() {
  echo "usage: $0 CONFIG_FILE --secret-file FILE [--resend-key-stdin] --jmap-url URL --admin-url URL --account-id ID --sender ADDRESS --external-recipient ADDRESS --approve-one-message [--output FILE] [--subject TEXT]" >&2
  exit 2
}
fail() { echo "resend certification refused: $1" >&2; exit 1; }

[ -n "$config_file" ] && [ -f "$config_file" ] || usage
shift
while [ "$#" -gt 0 ]; do
  case "$1" in
    --secret-file) [ "$#" -ge 2 ] || usage; secret_file="$2"; shift 2 ;;
    --resend-key-stdin) resend_key_stdin=1; shift ;;
    --jmap-url) [ "$#" -ge 2 ] || usage; jmap_url="$2"; shift 2 ;;
    --admin-url) [ "$#" -ge 2 ] || usage; admin_url="$2"; shift 2 ;;
    --account-id) [ "$#" -ge 2 ] || usage; account_id="$2"; shift 2 ;;
    --sender) [ "$#" -ge 2 ] || usage; sender="$2"; shift 2 ;;
    --external-recipient) [ "$#" -ge 2 ] || usage; external_recipient="$2"; shift 2 ;;
    --approve-one-message) operator_approval=1; shift ;;
    --output) [ "$#" -ge 2 ] || usage; sanitized_output="$2"; shift 2 ;;
    --subject) [ "$#" -ge 2 ] || usage; subject="$2"; shift 2 ;;
    *) usage ;;
  esac
done

[ "$operator_approval" -eq 1 ] || {
  echo "resend certification refused: --approve-one-message is required" >&2
  exit 1
}
[ -n "$secret_file" ] || fail "--secret-file is required for the protected Stalwart credentials"

env_value() {
  awk -F= -v key="$1" '$1 == key {sub(/^[^=]*=/, ""); sub(/\r$/, ""); print; exit}' "$config_file"
}
secret_value() {
  awk -F= -v key="$1" '$1 == key {sub(/^[^=]*=/, ""); sub(/\r$/, ""); print; exit}' "$secret_file"
}
require_config_value() {
  actual="$(env_value "$1")"
  test "$actual" = "$2" || { echo "resend certification refused: $1 must be $2" >&2; exit 1; }
}
require_command() {
  command -v "$1" >/dev/null 2>&1 || { echo "resend certification requires $1" >&2; exit 1; }
}

require_config_value DEPLOYMENT_TOPOLOGY smtp_gateway_vps_home_stalwart_resend
require_config_value AGENT_MAIL_DOMAIN agents.aiat.ca
require_config_value MAIL_HOSTNAME mail.aiat.ca
require_config_value OUTBOUND_RELAY_HOST smtp.resend.com
require_config_value OUTBOUND_RELAY_PORT 465
require_config_value OUTBOUND_RELAY_TLS_MODE implicit
require_config_value DIRECT_MX_OUTBOUND_ENABLED false
require_config_value DEFAULT_OUTBOUND_ENABLED false
require_config_value OUTBOUND_RELAY_CERTIFIED false

require_command awk
require_command curl
require_command jq
require_command grep
require_command openssl
require_command timeout
require_command stat
require_command mktemp

[ -f "$secret_file" ] || fail "missing protected secret file"
secret_mode="$(stat -c '%a' "$secret_file" 2>/dev/null || true)"
case "$secret_mode" in
  400|600) ;;
  *) fail "secret file must have mode 0400 or 0600" ;;
esac
secret_owner="$(stat -c '%u' "$secret_file" 2>/dev/null || true)"
current_uid="$(id -u)"
[ "$secret_owner" = 0 ] || [ "$secret_owner" = "$current_uid" ] || fail "secret file owner is not root or the current operator"

if [ "$resend_key_stdin" -eq 1 ]; then
  IFS= read -r resend_api_key || true
else
  resend_api_key="$(secret_value RESEND_API_KEY)"
fi
[ "${#resend_api_key}" -ge 20 ] || fail "RESEND_API_KEY was not supplied; the value is never recorded"

stalwart_api_key=""
stalwart_jmap_service_token=""
if [ -n "$secret_file" ]; then
  stalwart_api_key="$(secret_value STALWART_API_KEY)"
  stalwart_jmap_service_token="$(secret_value STALWART_JMAP_SERVICE_TOKEN)"
fi
[ -n "$stalwart_api_key" ] || fail "STALWART_API_KEY is required in the protected secret file"
[ -n "$stalwart_jmap_service_token" ] || fail "STALWART_JMAP_SERVICE_TOKEN is required in the protected secret file"

jmap_url="${jmap_url:-${STALWART_JMAP_URL:-}}"
admin_url="${admin_url:-${STALWART_ADMIN_URL:-}}"
account_id="${account_id:-${STALWART_ACCOUNT_ID:-}}"
sender="${sender:-${PRODUCTION_SENDER:-}}"
external_recipient="${external_recipient:-${EXTERNAL_RECIPIENT:-}}"
[ -n "$jmap_url" ] || fail "Stalwart JMAP URL is required"
[ -n "$admin_url" ] || fail "Stalwart management JMAP URL is required"
[ -n "$account_id" ] || fail "STALWART_ACCOUNT_ID is required"
printf '%s\n' "$sender" | grep -Eq '^[^[:space:]@]+@agents\.aiat\.ca$' || fail "sender must be a production @agents.aiat.ca address"
printf '%s\n' "$external_recipient" | grep -Eq '^[^[:space:]@]+@[^[:space:]@]+$' || fail "external recipient is malformed"
case "$external_recipient" in
  *@agents.aiat.ca) fail "external recipient must not be an agents.aiat.ca address" ;;
esac

case "$jmap_url$admin_url" in
  *[!A-Za-z0-9:/._?=%+-]*) fail "JMAP URLs contain unsafe characters" ;;
esac

route_log="$(mktemp)"
tls_log="$(mktemp)"
curl_config="$(mktemp)"
payload_file="$(mktemp)"
output_tmp=""
umask 077
cleanup() {
  rm -f "$route_log" "$tls_log" "$curl_config" "$payload_file"
  if [ -n "$output_tmp" ]; then rm -f "$output_tmp"; fi
}
trap cleanup EXIT INT TERM
chmod 600 "$route_log" "$tls_log" "$curl_config" "$payload_file"

# This read-only verifier rejects Mx/direct routes and requires the selected
# Stalwart strategy to be exactly resend-relay. The Resend key is not passed
# to it; STALWART_API_KEY is supplied only through its environment.
if ! STALWART_ADMIN_URL="$admin_url" STALWART_API_KEY="$stalwart_api_key" STALWART_ADMIN_INSECURE_TLS=false \
  sh "$base_dir/../../mail-edge/scripts/verify-stalwart-relay.sh" >"$route_log" 2>&1; then
  fail "Stalwart route verification failed; no message was sent"
fi

if ! timeout 20 openssl s_client -connect smtp.resend.com:465 -servername smtp.resend.com \
  -verify_return_error -brief </dev/null >"$tls_log" 2>&1; then
  fail "smtp.resend.com:465 TLS precheck failed; no message was sent"
fi
grep -Eq 'Verification:[[:space:]]+OK|Verify return code:[[:space:]]+0[[:space:]]+\(ok\)' "$tls_log" || \
  fail "smtp.resend.com:465 certificate verification failed; no message was sent"

printf '%s\n' "$stalwart_jmap_service_token" | grep -Eq '^(Bearer |Basic |OAuth )?[A-Za-z0-9._+/=-]+$' || \
  fail "Stalwart JMAP service token contains unsafe characters"
case "$stalwart_jmap_service_token" in
  Bearer\ *|Basic\ *|OAuth\ *) auth_header="$stalwart_jmap_service_token" ;;
  *) auth_header="Bearer $stalwart_jmap_service_token" ;;
esac
printf 'url = "%s/jmap"\nrequest = POST\nheader = "Authorization: %s"\nheader = "Content-Type: application/json"\nheader = "Accept: application/json"\n' \
  "$jmap_url" "$auth_header" >"$curl_config"

jmap() {
  printf '%s' "$1" >"$payload_file"
  curl --silent --show-error --fail --config "$curl_config" --data-binary "@$payload_file"
}

prerequisite_payload="$(jq -cn --arg account "$account_id" '{using:["urn:ietf:params:jmap:core","urn:ietf:params:jmap:mail","urn:ietf:params:jmap:submission"],methodCalls:[["Mailbox/get",{accountId:$account},"mailboxes"],["Identity/get",{accountId:$account},"identities"]]}')"
prerequisites="$(jmap "$prerequisite_payload")" || fail "Stalwart JMAP prerequisite lookup failed; no message was sent"
draft_id="$(printf '%s' "$prerequisites" | jq -r '.methodResponses[]? | select(.[0] == "Mailbox/get") | .[1].list[]? | select(.role == "drafts") | .id' | head -n 1)"
sent_id="$(printf '%s' "$prerequisites" | jq -r '.methodResponses[]? | select(.[0] == "Mailbox/get") | .[1].list[]? | select(.role == "sent") | .id' | head -n 1)"
identity_id="$(printf '%s' "$prerequisites" | jq -r --arg sender "$sender" '.methodResponses[]? | select(.[0] == "Identity/get") | .[1].list[]? | select((.email // "") | ascii_downcase == ($sender | ascii_downcase)) | .id' | head -n 1)"
[ -n "$draft_id" ] && [ "$draft_id" != null ] || fail "Stalwart Drafts mailbox was not found; no message was sent"
[ -n "$sent_id" ] && [ "$sent_id" != null ] || fail "Stalwart Sent mailbox was not found; no message was sent"
[ -n "$identity_id" ] && [ "$identity_id" != null ] || fail "Stalwart sender identity was not found; no message was sent"

run_id="$(date -u +%Y%m%dT%H%M%SZ)-$$"
email_id="resend-cert-email-$run_id"
submission_id="resend-cert-submission-$run_id"
subject="${subject:-AIAT Resend relay certification $run_id}"
body="AIAT one-message Resend relay certification $run_id. Reply to this message to complete certification."
submit_payload="$(jq -cn \
  --arg account "$account_id" --arg draft "$draft_id" --arg sent "$sent_id" --arg identity "$identity_id" \
  --arg sender "$sender" --arg recipient "$external_recipient" --arg subject "$subject" --arg body "$body" \
  --arg email_id "$email_id" --arg submission_id "$submission_id" \
  '{using:["urn:ietf:params:jmap:core","urn:ietf:params:jmap:mail","urn:ietf:params:jmap:submission"],methodCalls:[
    ["Email/set",{accountId:$account,create:{($email_id):{mailboxIds:{($draft):true},keywords:{"$draft":true},from:[{email:$sender}],to:[{email:$recipient}],subject:$subject,bodyStructure:{type:"text/plain",partId:"body"},bodyValues:{body:{value:$body,isTruncated:false}}}}},"create-email"],
    ["EmailSubmission/set",{accountId:$account,create:{($submission_id):{emailId:("#" + $email_id),identityId:$identity,envelope:{mailFrom:{email:$sender},rcptTo:[{email:$recipient}]}}},onSuccessUpdateEmail:{("#" + $submission_id):{("mailboxIds/" + $draft):null,("mailboxIds/" + $sent):true,"keywords/$draft":null,"keywords/$seen":true}}},"submit-email"]
  ]}')"
submission_response="$(jmap "$submit_payload")" || fail "Stalwart JMAP submission failed; delivery status is unknown"
printf '%s' "$submission_response" | jq -e --arg email_id "$email_id" --arg submission_id "$submission_id" '
  any(.methodResponses[]?; .[0] == "Email/set" and ((.[1].created // {}) | has($email_id))) and
  any(.methodResponses[]?; .[0] == "EmailSubmission/set" and ((.[1].created // {}) | has($submission_id)))
' >/dev/null || fail "Stalwart did not acknowledge the certification submission"

status_payload="$(jq -cn --arg account "$account_id" --arg submission_id "$submission_id" '{using:["urn:ietf:params:jmap:core","urn:ietf:params:jmap:submission"],methodCalls:[["EmailSubmission/get",{accountId:$account,ids:[$submission_id]},"submission-status"]]}')"
status_response="$(jmap "$status_payload")" || fail "Stalwart submission status lookup failed; delivery status is unknown"
printf '%s' "$status_response" | jq -e --arg submission_id "$submission_id" 'any(.methodResponses[]?; .[0] == "EmailSubmission/get" and any((.[1].list // [])[]?; .id == $submission_id))' >/dev/null || fail "Stalwart did not return the certification submission status"

if [ -n "$sanitized_output" ]; then
  output_tmp="${sanitized_output}.tmp.$$"
  umask 077
  {
    echo "CERTIFICATION_SCRIPT=aiat-smtp-gateway-certify-resend-v1"
    echo "TLS_PRECHECK=PASS"
    echo "STALWART_ROUTE=resend-relay"
    echo "DIRECT_MX_OUTBOUND_ENABLED=false"
    echo "STALWART_SUBMISSION_ID=$submission_id"
    echo "PROVIDER_MESSAGE_ID=$submission_id"
    echo "PRODUCTION_SENDER=$sender"
    echo "EXTERNAL_RECIPIENT=$external_recipient"
    echo "DELIVERY_STATUS=PENDING_EXTERNAL_CONFIRMATION"
    echo "REPLY_RECEIVED=PENDING_EXTERNAL_CONFIRMATION"
  } >"$output_tmp"
  mv -n "$output_tmp" "$sanitized_output" 2>/dev/null || fail "refusing to overwrite existing certification output"
  output_tmp=""
else
  echo "CERTIFICATION_SCRIPT=aiat-smtp-gateway-certify-resend-v1"
  echo "TLS_PRECHECK=PASS"
  echo "STALWART_ROUTE=resend-relay"
  echo "DIRECT_MX_OUTBOUND_ENABLED=false"
  echo "STALWART_SUBMISSION_ID=$submission_id"
  echo "PROVIDER_MESSAGE_ID=$submission_id"
  echo "PRODUCTION_SENDER=$sender"
  echo "EXTERNAL_RECIPIENT=$external_recipient"
  echo "DELIVERY_STATUS=PENDING_EXTERNAL_CONFIRMATION"
  echo "REPLY_RECEIVED=PENDING_EXTERNAL_CONFIRMATION"
fi

unset resend_api_key stalwart_api_key stalwart_jmap_service_token auth_header
echo "one approved Stalwart/JMAP certification message was submitted; external delivery and reply evidence are still required"
