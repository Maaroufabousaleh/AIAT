#!/bin/sh
set -eu

base_dir="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
env_file="${1:-$base_dir/.env.smtp-gateway}"
external_target=""
evidence_file=""
allow_certified=0
shift || true
while [ "$#" -gt 0 ]; do
  case "$1" in
    --external-target) external_target="${2:?--external-target needs a host or IP}"; shift 2 ;;
    --evidence) evidence_file="${2:?--evidence needs a file}"; shift 2 ;;
    --allow-certified) allow_certified=1; shift ;;
    *) echo "unknown preflight option: $1" >&2; exit 2 ;;
  esac
done
test -f "$env_file" || { echo "missing SMTP gateway environment file" >&2; exit 1; }

env_value() { awk -F= -v key="$1" '$1 == key {sub(/^[^=]*=/, ""); sub(/\r$/, ""); print; exit}' "$env_file"; }
require_value() {
  actual="$(env_value "$1")"
  test "$actual" = "$2" || { echo "preflight refused: $1 must be $2" >&2; exit 1; }
}
marker() {
  file="$1"; value="$2"
  test -f "$file" || { echo "preflight refused: missing evidence file $file" >&2; exit 1; }
  grep -Eq "^${value}=PASS$" "$file" || { echo "preflight refused: evidence marker ${value}=PASS is missing from $file" >&2; exit 1; }
}
real_ipv4() {
  ip="$1"
  printf '%s\n' "$ip" | awk -F. 'NF == 4 && $1 >= 1 && $1 <= 223 && $2 >= 0 && $2 <= 255 && $3 >= 0 && $3 <= 255 && $4 >= 1 && $4 <= 254 {ok=1} END {exit(ok ? 0 : 1)}' || return 1
  case "$ip" in
    10.*|127.*|169.254.*|192.168.*|172.1[6-9].*|172.2[0-9].*|172.3[0-1].*|100.6[4-9].*|100.[7-9][0-9].*|100.1[0-1][0-9].*|100.12[0-7].*|198.18.*|198.19.*|192.0.2.*|198.51.100.*|203.0.113.*) return 1 ;;
  esac
  return 0
}

require_value DEPLOYMENT_TOPOLOGY smtp_gateway_vps_home_stalwart_resend
require_value MAS_ENVIRONMENT production
require_value IDENTITY_PROFILE production
require_value AGENT_MAIL_DOMAIN agents.aiat.ca
require_value MAIL_HOSTNAME mail.aiat.ca
require_value IDENTITY_HOSTNAME identity.aiat.ca
require_value DIRECT_MX_OUTBOUND_ENABLED false
require_value DEFAULT_OUTBOUND_ENABLED false
require_value OUTBOUND_RELAY_HOST smtp.resend.com
require_value OUTBOUND_RELAY_PORT 465
require_value OUTBOUND_RELAY_TLS_MODE implicit
agent_domain="$(env_value AGENT_MAIL_DOMAIN)"
public_ip="$(env_value SMTP_GATEWAY_PUBLIC_IP)"
test "$(env_value PUBLIC_MAIL_IP)" = "$public_ip" || { echo "preflight refused: PUBLIC_MAIL_IP and SMTP_GATEWAY_PUBLIC_IP must match" >&2; exit 1; }
real_ipv4 "$public_ip" || { echo "preflight refused: SMTP_GATEWAY_PUBLIC_IP must be a real public IPv4" >&2; exit 1; }

certified="$(env_value OUTBOUND_RELAY_CERTIFIED)"
if [ "$allow_certified" -eq 0 ]; then
  test "$certified" = false || { echo "preflight refused: OUTBOUND_RELAY_CERTIFIED must remain false" >&2; exit 1; }
else
  test "$certified" = true || { echo "activation refused: OUTBOUND_RELAY_CERTIFIED=true is required only with live evidence" >&2; exit 1; }
fi

command -v nc >/dev/null 2>&1 || { echo "nc is required for external preflight" >&2; exit 1; }
command -v dig >/dev/null 2>&1 || { echo "dig is required for external preflight" >&2; exit 1; }
command -v curl >/dev/null 2>&1 || { echo "curl is required for external preflight" >&2; exit 1; }
command -v swaks >/dev/null 2>&1 || { echo "swaks is required for the fail-closed open-relay test" >&2; exit 1; }

target="${external_target:-$public_ip}"
for port in 25 80 443; do
  nc -z -w 15 "$target" "$port" || { echo "preflight refused: gateway TCP/$port is not externally reachable" >&2; exit 1; }
done

"$base_dir/scripts/validate-dns.sh" "$env_file"

for name in "$(env_value MAIL_HOSTNAME)" "$(env_value IDENTITY_HOSTNAME)"; do
  code="$(curl -sS -o /dev/null -w '%{http_code}' --connect-timeout 15 --max-time 20 "https://$name/")" || { echo "preflight refused: public TLS failed for $name" >&2; exit 1; }
  test "$code" != 000 || { echo "preflight refused: no HTTPS response for $name" >&2; exit 1; }
done
admin_code="$(curl -sS -o /dev/null -w '%{http_code}' --connect-timeout 15 --max-time 20 "https://$(env_value MAIL_HOSTNAME)/admin")" || true
test "$admin_code" = 404 || { echo "preflight refused: Stalwart administration is publicly reachable" >&2; exit 1; }

for port in 5432 8010 8080 18080 2375 2376; do
  if nc -z -w 3 "$target" "$port" >/dev/null 2>&1; then
    echo "preflight refused: management/internal TCP/$port is externally reachable" >&2
    exit 1
  fi
done

relay_probe="$(swaks --server "$target" --port 25 --from "preflight@$agent_domain" --to "preflight@example.net" --quit-after RCPT --timeout 15 2>&1 || true)"
printf '%s\n' "$relay_probe" | grep -Eq '250[[:space:]]+2\.[0-9]\.[0-9]' && { echo "preflight refused: gateway accepted an external relay recipient" >&2; exit 1; }

home_ip="$(env_value HOME_WIREGUARD_IP)"
nc -z -w 10 "$home_ip" 25 || { echo "preflight refused: gateway-to-home SMTP is unreachable over WireGuard" >&2; exit 1; }
home_probe="$(swaks --server "$home_ip" --port "$(env_value HOME_STALWART_SMTP_PORT)" --from "preflight@$agent_domain" --to "postmaster@$agent_domain" --quit-after RCPT --timeout 15 2>&1 || true)"
printf '%s\n' "$home_probe" | grep -Eq '250[[:space:]]+2\.[0-9]\.[0-9]' || { echo "preflight refused: home Stalwart did not accept the gateway-path recipient" >&2; exit 1; }

home_evidence="$(env_value HOME_PREFLIGHT_EVIDENCE)"
marker "$home_evidence" HOME_GATEWAY_BINDINGS
marker "$home_evidence" HOME_PUBLIC_TCP25_BLOCKED
marker "$home_evidence" HOME_MANAGEMENT_PORTS
marker "$home_evidence" RESEND_SMTP_465
marker "$home_evidence" DIRECT_OUTBOUND_TCP25_BLOCKED

"$base_dir/scripts/validate-gateway-firewall.sh" "$env_file"
"$base_dir/scripts/validate-queue-disk.sh" "$env_file"
if [ "$allow_certified" -eq 1 ]; then
  test -n "$evidence_file" || evidence_file="$(env_value GATEWAY_EXTERNAL_PREFLIGHT_EVIDENCE)"
  marker "$evidence_file" EXTERNAL_TCP25_GATEWAY
  marker "$evidence_file" PUBLIC_TLS
  marker "$evidence_file" NO_OPEN_RELAY
  marker "$evidence_file" WIREGUARD_HANDSHAKE
  marker "$evidence_file" GATEWAY_TO_STALWART_SMTP
  staging="$(env_value GATEWAY_STAGING_EVIDENCE)"
  marker "$staging" GATEWAY_QUEUE_PERSISTENCE
  marker "$staging" OFFLINE_QUEUE_RETRY
  marker "$staging" E2E_SMTP_JMAP
  marker "$staging" RESEND_RELAY_CERTIFIED
fi

echo "SMTP gateway preflight passed all configured live gates; activation evidence is complete."
