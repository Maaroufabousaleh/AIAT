#!/bin/sh
# Verify the default Cloudflare/Resend DNS expectations without reading or
# printing a secret. Every provider-specific value is supplied by the
# operator because Cloudflare and Resend return account/region-specific data.
set -eu

: "${AGENT_MAIL_DOMAIN:=agents.aiat.ca}"
: "${IDENTITY_HOSTNAME:=identity.aiat.ca}"
: "${CLOUDFLARE_EMAIL_ROUTING_MX_RECORDS:?set the exact Cloudflare Email Routing MX records as priority:host,priority:host}"
: "${RESEND_SPF_HOST:?set the Resend-verified SPF record hostname}"
: "${RESEND_SPF_TXT_EXPECTED:?set the exact Resend-issued SPF TXT value without outer quotes}"
: "${RESEND_DKIM_HOST:?set the exact Resend DKIM hostname}"
: "${RESEND_DKIM_TYPE:?set RESEND_DKIM_TYPE to TXT or CNAME}"
: "${RESEND_DKIM_EXPECTED:?set the exact Resend-issued DKIM TXT value or CNAME target}"
: "${RESEND_RETURN_PATH_HOST:?set the exact Resend return-path hostname}"
: "${RESEND_RETURN_PATH_MX_RECORDS:?set the exact Resend return-path MX records as priority:host,priority:host}"
: "${RESEND_RETURN_PATH_TXT_EXPECTED:?set the exact Resend return-path TXT value without outer quotes}"
: "${RESEND_DMARC_HOST:=_dmarc.$AGENT_MAIL_DOMAIN}"
: "${RESEND_DMARC_TXT_EXPECTED:=}"

command -v dig >/dev/null 2>&1 || {
  echo "dig is required for default Cloudflare/Resend DNS validation" >&2
  exit 1
}

test "$AGENT_MAIL_DOMAIN" = "agents.aiat.ca" || {
  echo "DNS validation refused: AGENT_MAIL_DOMAIN must be agents.aiat.ca" >&2
  exit 1
}

case "$RESEND_DKIM_TYPE" in
  TXT|CNAME) ;;
  *) echo "DNS validation refused: RESEND_DKIM_TYPE must be TXT or CNAME" >&2; exit 1 ;;
esac

normalize_mx_list() {
  printf '%s\n' "$1" |
    tr ',' '\n' |
    sed 's/[[:space:]]*:[[:space:]]*/:/g; s/[[:space:]]//g; s/[.]$//' |
    sed '/^$/d' |
    sort
}

actual_mx_list() {
  dig +short MX "$1" |
    awk '{target=$2; sub(/[.]$/, "", target); if ($1 != "" && target != "") print $1 ":" target}' |
    sort
}

check_mx_records() {
  host="$1"
  expected="$2"
  actual="$(actual_mx_list "$host")"
  expected_normalized="$(normalize_mx_list "$expected")"
  test -n "$actual" && test "$actual" = "$expected_normalized" || {
    echo "MX records for $host do not match the exact operator-supplied expectation" >&2
    echo "expected: $expected_normalized" >&2
    echo "observed: ${actual:-<none>}" >&2
    exit 1
  }
}

check_txt() {
  host="$1"
  expected="$2"
  actual="$(dig +short TXT "$host" | tr -d '"' | sed '/^$/d')"
  printf '%s\n' "$actual" | grep -F -x -- "$expected" >/dev/null || {
    echo "TXT record for $host does not match the exact operator-supplied expectation" >&2
    exit 1
  }
}

check_cname() {
  host="$1"
  expected="$2"
  actual="$(dig +short CNAME "$host" | sed 's/[.]$//' | sed '/^$/d')"
  printf '%s\n' "$actual" | grep -F -x -- "$expected" >/dev/null || {
    echo "CNAME record for $host does not match the exact operator-supplied expectation" >&2
    exit 1
  }
}

check_mx_records "$AGENT_MAIL_DOMAIN" "$CLOUDFLARE_EMAIL_ROUTING_MX_RECORDS"

identity_records="$(
  {
    dig +short A "$IDENTITY_HOSTNAME"
    dig +short AAAA "$IDENTITY_HOSTNAME"
    dig +short CNAME "$IDENTITY_HOSTNAME"
  } | sed '/^$/d'
)"
test -n "$identity_records" || {
  echo "identity hostname has no A, AAAA, or CNAME record: $IDENTITY_HOSTNAME" >&2
  exit 1
}

check_txt "$RESEND_SPF_HOST" "$RESEND_SPF_TXT_EXPECTED"
if [ "$RESEND_DKIM_TYPE" = "TXT" ]; then
  check_txt "$RESEND_DKIM_HOST" "$RESEND_DKIM_EXPECTED"
else
  check_cname "$RESEND_DKIM_HOST" "$RESEND_DKIM_EXPECTED"
fi
check_mx_records "$RESEND_RETURN_PATH_HOST" "$RESEND_RETURN_PATH_MX_RECORDS"
check_txt "$RESEND_RETURN_PATH_HOST" "$RESEND_RETURN_PATH_TXT_EXPECTED"

if [ -n "$RESEND_DMARC_TXT_EXPECTED" ]; then
  check_txt "$RESEND_DMARC_HOST" "$RESEND_DMARC_TXT_EXPECTED"
else
  dmarc="$(dig +short TXT "$RESEND_DMARC_HOST" | tr -d '"' | sed '/^$/d')"
  printf '%s\n' "$dmarc" | grep -Eiq '^v=DMARC1([;[:space:]]|$)' || {
    echo "DMARC TXT record is missing for $RESEND_DMARC_HOST" >&2
    exit 1
  }
fi

echo "Cloudflare Email Routing MX, identity hostname, Resend SPF/DKIM/return-path, and DMARC DNS expectations passed."
echo "The exact *@$AGENT_MAIL_DOMAIN -> deployed Worker route still requires Cloudflare dashboard/API verification."
