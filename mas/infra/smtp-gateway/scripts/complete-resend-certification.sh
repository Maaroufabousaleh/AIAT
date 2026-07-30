#!/bin/sh
# Convert a pending local submission record into final evidence only after
# operator-observed provider, external delivery, and correlated reply evidence.
set -eu

pending_record="${1:-}"
output=""
provider_message_id=""
external_receipt_id=""
reply_message_id=""
reply_in_reply_to=""
reply_token_stdin=0
approved=0

usage() {
  echo "usage: $0 PENDING_RECORD --output FINAL_EVIDENCE --resend-provider-message-id ID --external-receipt-id ID --reply-message-id RFC5322_ID --reply-in-reply-to ORIGINAL_ID --reply-token-stdin --approve-completion" >&2
  exit 2
}
fail() { echo "Resend certification completion refused: $1" >&2; exit 1; }

[ -f "$pending_record" ] || usage
shift
while [ "$#" -gt 0 ]; do
  case "$1" in
    --output) [ "$#" -ge 2 ] || usage; output="$2"; shift 2 ;;
    --resend-provider-message-id) [ "$#" -ge 2 ] || usage; provider_message_id="$2"; shift 2 ;;
    --external-receipt-id) [ "$#" -ge 2 ] || usage; external_receipt_id="$2"; shift 2 ;;
    --reply-message-id) [ "$#" -ge 2 ] || usage; reply_message_id="$2"; shift 2 ;;
    --reply-in-reply-to) [ "$#" -ge 2 ] || usage; reply_in_reply_to="$2"; shift 2 ;;
    --reply-token-stdin) reply_token_stdin=1; shift ;;
    --approve-completion) approved=1; shift ;;
    *) usage ;;
  esac
done

[ "$approved" -eq 1 ] || fail "--approve-completion is required"
[ "$reply_token_stdin" -eq 1 ] || fail "--reply-token-stdin is required"
[ -n "$output" ] || fail "--output is required"
test ! -e "$output" || fail "refusing to overwrite final evidence"
for command in awk grep sha256sum date; do command -v "$command" >/dev/null 2>&1 || fail "$command is required"; done

value() { awk -F= -v key="$1" '$1 == key {sub(/^[^=]*=/, ""); sub(/\r$/, ""); print; exit}' "$pending_record"; }
require_value() { test "$(value "$1")" = "$2" || fail "$1 must be $2 in the pending record"; }
require_pattern() { printf '%s\n' "$(value "$1")" | grep -Eq "$2" || fail "missing or malformed $1 in the pending record"; }

require_value RESEND_CERTIFICATION_SUBMISSION PASS
require_value CERTIFICATION_STATE PENDING_EXTERNAL_CONFIRMATION
require_value CERTIFICATION_SCOPE local_wsl_loopback
require_value JMAP_URL http://127.0.0.1:18080
require_value STALWART_ROUTE resend-relay
require_value DIRECT_MX_OUTBOUND_ENABLED false
require_value RESEND_PROVIDER_MESSAGE_ID PENDING_EXTERNAL_PROVIDER_CORRELATION
require_value DELIVERY_STATUS PENDING_EXTERNAL_RECEIPT
require_value REPLY_RECEIVED PENDING_EXTERNAL_REPLY
require_pattern STALWART_SUBMISSION_ID '^[[:print:]]{5,}$'
require_pattern ORIGINAL_MESSAGE_ID '^<[^[:space:]<>]+>$'
require_pattern PRODUCTION_SENDER '^[^[:space:]@]+@agents\.aiat\.ca$'
require_pattern EXTERNAL_RECIPIENT '^[^[:space:]@]+@[^[:space:]@]+$'
require_pattern REPLY_TOKEN_SHA256 '^[a-f0-9]{64}$'

IFS= read -r reply_token || true
[ -n "$reply_token" ] || fail "reply token is required on stdin"
reply_token_sha256="$(printf %s "$reply_token" | sha256sum | awk '{print $1}')"
test "$reply_token_sha256" = "$(value REPLY_TOKEN_SHA256)" || fail "reply token does not match the pending external message"

printf '%s\n' "$provider_message_id" | grep -Eq '^[[:print:]]{5,}$' || fail "actual Resend provider message id is required"
test "$provider_message_id" != "$(value STALWART_SUBMISSION_ID)" || fail "Resend provider id must not equal the local Stalwart submission id"
printf '%s\n' "$external_receipt_id" | grep -Eq '^[[:print:]]{5,}$' || fail "external mailbox receipt correlation is required"
printf '%s\n' "$reply_message_id" | grep -Eq '^<[^[:space:]<>]+>$' || fail "reply Message-ID is malformed"
test "$reply_in_reply_to" = "$(value ORIGINAL_MESSAGE_ID)" || fail "reply must correlate to the original Message-ID"

tmp_output="${output}.tmp.$$"
umask 077
cleanup() { rm -f "$tmp_output"; unset reply_token reply_token_sha256; }
trap cleanup EXIT INT TERM
{
  echo "RESEND_OUTBOUND_RELAY_CERTIFIED=PASS"
  echo "RELAY_HOST=smtp.resend.com"
  echo "RELAY_PORT=465"
  echo "TLS_MODE=implicit"
  echo "TLS_VERIFICATION=PASS"
  echo "SMTP_AUTHENTICATION=PASS"
  echo "AUTH_USERNAME=resend"
  echo "PRODUCTION_SENDER=$(value PRODUCTION_SENDER)"
  echo "EXTERNAL_RECIPIENT=$(value EXTERNAL_RECIPIENT)"
  echo "STALWART_ROUTE=resend-relay"
  echo "DIRECT_MX_OUTBOUND_ENABLED=false"
  echo "STALWART_SUBMISSION_ID=$(value STALWART_SUBMISSION_ID)"
  echo "RESEND_PROVIDER_MESSAGE_ID=$provider_message_id"
  echo "ORIGINAL_MESSAGE_ID=$(value ORIGINAL_MESSAGE_ID)"
  echo "EXTERNAL_RECEIPT_ID=$external_receipt_id"
  echo "DELIVERY_STATUS=delivered"
  echo "REPLY_RECEIVED=PASS"
  echo "REPLY_MESSAGE_ID=$reply_message_id"
  echo "REPLY_IN_REPLY_TO=$reply_in_reply_to"
  echo "REPLY_TOKEN_VERIFIED=PASS"
  echo "CERTIFIED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} >"$tmp_output"
chmod 600 "$tmp_output"
mv "$tmp_output" "$output"
tmp_output=""
echo "final Resend evidence written from pending submission plus externally observed provider, delivery, and reply correlations"
