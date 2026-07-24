#!/bin/sh
# Verify public certificate trust, hostname matching, and minimum remaining life.
set -eu

: "${MAIL_HOSTNAME:=mail.aiat.ca}"
: "${IDENTITY_HOSTNAME:=identity.aiat.ca}"
command -v openssl >/dev/null 2>&1 || { echo "openssl is required" >&2; exit 1; }
command -v curl >/dev/null 2>&1 || { echo "curl is required" >&2; exit 1; }

check_tls() {
  host="$1"
  timeout 15 openssl s_client -connect "$host:443" -servername "$host" \
    -verify_hostname "$host" -verify_return_error </dev/null >/dev/null 2>&1 || {
      echo "TLS trust or hostname validation failed for $host" >&2; exit 1;
    }
  timeout 15 openssl s_client -connect "$host:443" -servername "$host" </dev/null 2>/dev/null \
    | openssl x509 -checkend 86400 -noout >/dev/null || {
      echo "TLS certificate for $host expires within 24 hours" >&2; exit 1;
    }
}

check_tls "$MAIL_HOSTNAME"
check_tls "$IDENTITY_HOSTNAME"
curl -fsS --max-time 15 "https://$IDENTITY_HOSTNAME/healthz" >/dev/null || {
  echo "public identity health endpoint is unavailable over TLS" >&2; exit 1;
}
echo "Public mail and identity TLS certificates passed trust, hostname, and expiry checks."
