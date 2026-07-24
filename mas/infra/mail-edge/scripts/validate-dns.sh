#!/bin/sh
# Verify the public DNS/PTR prerequisites without reading any secret file.
set -eu

: "${PRIMARY_DOMAIN:=aiat.ca}"
: "${AGENT_MAIL_DOMAIN:=agents.aiat.ca}"
: "${MAIL_HOSTNAME:=mail.aiat.ca}"
: "${IDENTITY_HOSTNAME:=identity.aiat.ca}"
: "${PUBLIC_MAIL_IP:?PUBLIC_MAIL_IP is required for PTR validation}"
: "${DKIM_SELECTOR:=resend}"
command -v dig >/dev/null 2>&1 || { echo "dig is required" >&2; exit 1; }

check_mx() {
  domain="$1"
  dig +short MX "$domain" | sed 's/[.]$//' | awk '{print $2}' | grep -Fx "$MAIL_HOSTNAME" >/dev/null || {
    echo "MX for $domain must point to $MAIL_HOSTNAME" >&2; exit 1;
  }
}

mail_ip="$(dig +short A "$MAIL_HOSTNAME" | head -n 1)"
test "$mail_ip" = "$PUBLIC_MAIL_IP" || { echo "A record for $MAIL_HOSTNAME must be $PUBLIC_MAIL_IP" >&2; exit 1; }
identity_ip="$(dig +short A "$IDENTITY_HOSTNAME" | head -n 1)"
test "$identity_ip" = "$PUBLIC_MAIL_IP" || { echo "A record for $IDENTITY_HOSTNAME must be $PUBLIC_MAIL_IP" >&2; exit 1; }
check_mx "$AGENT_MAIL_DOMAIN"
dig +short -x "$PUBLIC_MAIL_IP" | sed 's/[.]$//' | grep -Fx "$MAIL_HOSTNAME" >/dev/null || { echo "PTR for $PUBLIC_MAIL_IP must be $MAIL_HOSTNAME" >&2; exit 1; }
dig +short TXT "$AGENT_MAIL_DOMAIN" | tr -d '"' | grep -qi 'v=spf1' || { echo "SPF TXT record is missing for $AGENT_MAIL_DOMAIN" >&2; exit 1; }
dig +short TXT "_dmarc.$AGENT_MAIL_DOMAIN" | tr -d '"' | grep -qi 'v=DMARC1' || { echo "DMARC TXT record is missing for $AGENT_MAIL_DOMAIN" >&2; exit 1; }
dkim_name="$DKIM_SELECTOR._domainkey.$AGENT_MAIL_DOMAIN"
if ! dig +short TXT "$dkim_name" | tr -d '"' | grep -qi 'p='; then
  dig +short CNAME "$dkim_name" | grep -Eq '[.]$' || {
    echo "DKIM TXT/CNAME record is missing for $AGENT_MAIL_DOMAIN" >&2
    exit 1
  }
fi
echo "DNS, PTR, SPF, DKIM, and DMARC prerequisites passed."
