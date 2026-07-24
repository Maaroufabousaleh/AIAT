#!/bin/sh
set -eu

command -v ss >/dev/null 2>&1
ss -ltn | grep -Eq '(:25|:443)' || { echo "expected SMTP/HTTPS listeners are absent" >&2; exit 1; }
if command -v iptables >/dev/null 2>&1; then
  iptables -S OUTPUT | grep -Eq -- '--dport 25.*(REJECT|DROP)' || { echo "host firewall must reject outbound tcp/25" >&2; exit 1; }
fi
timeout 10 bash -c ':</dev/tcp/smtp.resend.com/465' 2>/dev/null || { echo "Resend SMTP/465 is unreachable" >&2; exit 1; }
: "${DIRECT_MX_TEST_HOST:=gmail-smtp-in.l.google.com}"
if timeout 10 bash -c ':</dev/tcp/$1/25' _ "$DIRECT_MX_TEST_HOST" 2>/dev/null; then
  echo "direct outbound MX tcp/25 is reachable and must be blocked" >&2
  exit 1
fi
echo "Host checks and active outbound tcp/25 rejection passed; verify OCI NSG ingress 25/443 separately."
