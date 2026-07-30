#!/bin/sh
# Submit exactly one approved message through the local WSL Stalwart JMAP path.
# A successful submission is intentionally recorded as pending: it is not a
# Resend provider acceptance or external delivery claim.
set -eu

base_dir="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
profile="${1:-}"
secret_file=""
relay_secret_file=""
container=""
account_id=""
sender=""
external_recipient=""
output=""
jmap_url="http://127.0.0.1:18080"
admin_url="http://127.0.0.1:18080/api"
subject=""

usage() {
  echo "usage: $0 PROFILE --secret-file FILE [--relay-secret-file FILE] --stalwart-container NAME --account-id ID --sender ADDRESS --external-recipient ADDRESS --approve-one-message --output PENDING_RECORD [--jmap-url http://127.0.0.1:18080 --admin-url http://127.0.0.1:18080/api --subject TEXT]" >&2
  exit 2
}
fail() { echo "Resend certification submission refused: $1" >&2; exit 1; }

[ -f "$profile" ] || usage
shift
approved=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --secret-file) [ "$#" -ge 2 ] || usage; secret_file="$2"; shift 2 ;;
    --relay-secret-file) [ "$#" -ge 2 ] || usage; relay_secret_file="$2"; shift 2 ;;
    --stalwart-container) [ "$#" -ge 2 ] || usage; container="$2"; shift 2 ;;
    --account-id) [ "$#" -ge 2 ] || usage; account_id="$2"; shift 2 ;;
    --sender) [ "$#" -ge 2 ] || usage; sender="$2"; shift 2 ;;
    --external-recipient) [ "$#" -ge 2 ] || usage; external_recipient="$2"; shift 2 ;;
    --approve-one-message) approved=1; shift ;;
    --output) [ "$#" -ge 2 ] || usage; output="$2"; shift 2 ;;
    --jmap-url) [ "$#" -ge 2 ] || usage; jmap_url="$2"; shift 2 ;;
    --admin-url) [ "$#" -ge 2 ] || usage; admin_url="$2"; shift 2 ;;
    --subject) [ "$#" -ge 2 ] || usage; subject="$2"; shift 2 ;;
    *) usage ;;
  esac
done

[ "$approved" -eq 1 ] || fail "--approve-one-message is required"
[ -n "$output" ] || fail "--output is required"
test ! -e "$output" || fail "refusing to overwrite an existing pending record"
test "$jmap_url" = http://127.0.0.1:18080 || fail "JMAP must remain local at http://127.0.0.1:18080"
test "$admin_url" = http://127.0.0.1:18080/api || fail "admin JMAP must remain local at http://127.0.0.1:18080/api"
for command in awk curl jq openssl grep sha256sum stat mktemp; do command -v "$command" >/dev/null 2>&1 || fail "$command is required"; done

preflight_log="$(mktemp)"
cleanup() { rm -f "$preflight_log" "$curl_config" "$payload_file" "$tmp_output"; }
curl_config=""
payload_file=""
tmp_output=""
trap cleanup EXIT INT TERM

set -- "$base_dir/scripts/preflight-resend-certification.sh" "$profile" \
  --secret-file "$secret_file" --stalwart-container "$container" \
  --account-id "$account_id" --sender "$sender"
if [ -n "$relay_secret_file" ]; then
  set -- "$@" --relay-secret-file "$relay_secret_file"
fi
set -- "$@" --jmap-url "$jmap_url" --admin-url "$admin_url"
"$@" >"$preflight_log"

secret_value() { awk -F= -v key="$1" '$1 == key {sub(/^[^=]*=/, ""); sub(/\r$/, ""); print; exit}' "$secret_file"; }
service_token="$(secret_value STALWART_JMAP_SERVICE_TOKEN)"
[ -n "$service_token" ] || fail "STALWART_JMAP_SERVICE_TOKEN is missing"
printf '%s\n' "$external_recipient" | grep -Eq '^[^[:space:]@]+@[^[:space:]@]+$' || fail "external recipient is malformed"
case "$external_recipient" in *@agents.aiat.ca) fail "external recipient must be outside agents.aiat.ca" ;; esac

umask 077
curl_config="$(mktemp)"
payload_file="$(mktemp)"
chmod 600 "$curl_config" "$payload_file"
case "$service_token" in Bearer\ *|Basic\ *|OAuth\ *) auth_header="$service_token" ;; *) auth_header="Bearer $service_token" ;; esac
printf 'url = "http://127.0.0.1:18080/jmap"\nrequest = POST\nheader = "Authorization: %s"\nheader = "Content-Type: application/json"\n' "$auth_header" >"$curl_config"
jmap() { printf '%s' "$1" >"$payload_file"; curl --silent --show-error --fail --config "$curl_config" --data-binary "@$payload_file"; }

prerequisite_payload="$(jq -cn --arg account "$account_id" '{using:["urn:ietf:params:jmap:core","urn:ietf:params:jmap:mail","urn:ietf:params:jmap:submission"],methodCalls:[["Mailbox/get",{accountId:$account},"mailboxes"],["Identity/get",{accountId:$account},"identities"]]}')"
prerequisites="$(jmap "$prerequisite_payload")" || fail "local JMAP prerequisite lookup failed; no message was sent"
draft_id="$(printf '%s' "$prerequisites" | jq -r '.methodResponses[]? | select(.[0] == "Mailbox/get") | .[1].list[]? | select(.role == "drafts") | .id' | head -n 1)"
sent_id="$(printf '%s' "$prerequisites" | jq -r '.methodResponses[]? | select(.[0] == "Mailbox/get") | .[1].list[]? | select(.role == "sent") | .id' | head -n 1)"
identity_id="$(printf '%s' "$prerequisites" | jq -r --arg sender "$sender" '.methodResponses[]? | select(.[0] == "Identity/get") | .[1].list[]? | select((.email // "") | ascii_downcase == ($sender | ascii_downcase)) | .id' | head -n 1)"
[ -n "$draft_id" ] && [ "$draft_id" != null ] || fail "Stalwart Drafts mailbox was not found"
[ -n "$sent_id" ] && [ "$sent_id" != null ] || fail "Stalwart Sent mailbox was not found"
[ -n "$identity_id" ] && [ "$identity_id" != null ] || fail "Stalwart sender identity was not found"

run_id="$(date -u +%Y%m%dT%H%M%SZ)-$$"
reply_token="$(openssl rand -hex 24)"
reply_token_sha256="$(printf %s "$reply_token" | sha256sum | awk '{print $1}')"
email_creation_id="resend-cert-email-$run_id"
submission_creation_id="resend-cert-submission-$run_id"
subject="${subject:-AIAT Resend relay certification $run_id}"
body="AIAT one-message Resend relay certification. Reply with token $reply_token to complete delivery certification."
submit_payload="$(jq -cn \
  --arg account "$account_id" --arg draft "$draft_id" --arg sent "$sent_id" --arg identity "$identity_id" \
  --arg sender "$sender" --arg recipient "$external_recipient" --arg subject "$subject" --arg body "$body" \
  --arg email_creation_id "$email_creation_id" --arg submission_creation_id "$submission_creation_id" \
  '{using:["urn:ietf:params:jmap:core","urn:ietf:params:jmap:mail","urn:ietf:params:jmap:submission"],methodCalls:[
    ["Email/set",{accountId:$account,create:{($email_creation_id):{mailboxIds:{($draft):true},keywords:{"$draft":true},from:[{email:$sender}],to:[{email:$recipient}],subject:$subject,bodyStructure:{type:"text/plain",partId:"body"},bodyValues:{body:{value:$body,isTruncated:false}}}}},"create-email"],
    ["EmailSubmission/set",{accountId:$account,create:{($submission_creation_id):{emailId:("#" + $email_creation_id),identityId:$identity,envelope:{mailFrom:{email:$sender},rcptTo:[{email:$recipient}]}}},onSuccessUpdateEmail:{("#" + $submission_creation_id):{("mailboxIds/" + $draft):null,("mailboxIds/" + $sent):true,"keywords/$draft":null,"keywords/$seen":true}}},"submit-email"]
  ]}')"
submission_response="$(jmap "$submit_payload")" || fail "Stalwart JMAP submission failed; do not retry without inspecting local state"
stalwart_submission_id="$(printf '%s' "$submission_response" | jq -r --arg key "$submission_creation_id" '.methodResponses[]? | select(.[0] == "EmailSubmission/set") | .[1].created[$key].id // empty' | head -n 1)"
email_id="$(printf '%s' "$submission_response" | jq -r --arg key "$email_creation_id" '.methodResponses[]? | select(.[0] == "Email/set") | .[1].created[$key].id // empty' | head -n 1)"
[ -n "$stalwart_submission_id" ] || fail "Stalwart did not return a local submission id"
[ -n "$email_id" ] || fail "Stalwart did not return a local email id"

message_id_payload="$(jq -cn --arg account "$account_id" --arg email "$email_id" '{using:["urn:ietf:params:jmap:core","urn:ietf:params:jmap:mail"],methodCalls:[["Email/get",{accountId:$account,ids:[$email],properties:["messageId"]},"message-id"]]}')"
message_id_response="$(jmap "$message_id_payload")" || fail "message was submitted but its original Message-ID could not be read; do not complete certification"
original_message_id="$(printf '%s' "$message_id_response" | jq -r '.methodResponses[]? | select(.[0] == "Email/get") | .[1].list[0].messageId[0] // empty' | head -n 1)"
printf '%s\n' "$original_message_id" | grep -Eq '^<[^[:space:]<>]+>$' || fail "message was submitted but Stalwart did not expose an RFC Message-ID; do not complete certification"

tmp_output="${output}.tmp.$$"
{
  echo "RESEND_CERTIFICATION_SUBMISSION=PASS"
  echo "CERTIFICATION_STATE=PENDING_EXTERNAL_CONFIRMATION"
  echo "CERTIFICATION_SCOPE=local_wsl_loopback"
  echo "JMAP_URL=http://127.0.0.1:18080"
  echo "STALWART_SUBMISSION_ID=$stalwart_submission_id"
  echo "RESEND_PROVIDER_MESSAGE_ID=PENDING_EXTERNAL_PROVIDER_CORRELATION"
  echo "ORIGINAL_MESSAGE_ID=$original_message_id"
  echo "PRODUCTION_SENDER=$sender"
  echo "EXTERNAL_RECIPIENT=$external_recipient"
  echo "STALWART_ROUTE=resend-relay"
  echo "DIRECT_MX_OUTBOUND_ENABLED=false"
  echo "REPLY_TOKEN_SHA256=$reply_token_sha256"
  echo "DELIVERY_STATUS=PENDING_EXTERNAL_RECEIPT"
  echo "REPLY_RECEIVED=PENDING_EXTERNAL_REPLY"
} >"$tmp_output"
chmod 600 "$tmp_output"
mv "$tmp_output" "$output"
tmp_output=""
unset service_token auth_header reply_token
echo "one approved local Stalwart submission was recorded as pending; it is not provider or delivery certification"
