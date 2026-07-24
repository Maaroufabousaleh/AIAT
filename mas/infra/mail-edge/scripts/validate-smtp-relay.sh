#!/bin/sh
# Check that the allowed relay path is reachable with implicit TLS.
set -eu

: "${OUTBOUND_RELAY_HOST:=smtp.resend.com}"
: "${OUTBOUND_RELAY_PORT:=465}"
command -v openssl >/dev/null 2>&1 || { echo "openssl is required" >&2; exit 1; }
test "$OUTBOUND_RELAY_HOST" = "smtp.resend.com" || { echo "only smtp.resend.com is approved" >&2; exit 1; }
test "$OUTBOUND_RELAY_PORT" = "465" || { echo "this profile requires implicit TLS on 465" >&2; exit 1; }
timeout 15 openssl s_client -connect "$OUTBOUND_RELAY_HOST:$OUTBOUND_RELAY_PORT" -servername "$OUTBOUND_RELAY_HOST" -brief </dev/null 2>&1 | grep -q 'Protocol version' || { echo "Resend implicit TLS relay check failed" >&2; exit 1; }
echo "Resend SMTP relay TLS is reachable."
