#!/bin/sh
set -eu

base_dir="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
env_file="${1:-$base_dir/.env.smtp-gateway}"
test -f "$env_file" || { echo "missing SMTP gateway environment file" >&2; exit 1; }

env_value() { awk -F= -v key="$1" '$1 == key {sub(/^[^=]*=/, ""); sub(/\r$/, ""); print; exit}' "$env_file"; }
WG_INTERFACE="$(env_value WIREGUARD_INTERFACE)"
HOME_WG_IP="$(env_value HOME_WIREGUARD_IP)"
WG_MAX_HANDSHAKE_AGE_SECONDS="${WG_MAX_HANDSHAKE_AGE_SECONDS:-180}"
SSH_ALLOWED_CIDRS="$(env_value SSH_ALLOWED_CIDRS)"
test -n "$WG_INTERFACE" || { echo "WIREGUARD_INTERFACE is required" >&2; exit 1; }
test -n "$HOME_WG_IP" || { echo "HOME_WIREGUARD_IP is required" >&2; exit 1; }
test "$SSH_ALLOWED_CIDRS" != "<operator-admin-CIDR>" || { echo "replace SSH_ALLOWED_CIDRS with an operator allow-list" >&2; exit 1; }

command -v ss >/dev/null 2>&1 || { echo "ss is required" >&2; exit 1; }
command -v wg >/dev/null 2>&1 || { echo "wg is required" >&2; exit 1; }
command -v nc >/dev/null 2>&1 || { echo "nc is required" >&2; exit 1; }
for port in 25 80 443; do
  ss -ltn | grep -Eq ":$port[[:space:]]" || { echo "gateway must listen on TCP/$port" >&2; exit 1; }
done
if ss -ltn | grep -Eq '(:5432|:8010|:8080|:18080|:2375|:2376)[[:space:]]'; then
  echo "gateway management/internal listener is exposed" >&2
  exit 1
fi

handshake="$(wg show "$WG_INTERFACE" latest-handshakes 2>/dev/null || true)"
test -n "$handshake" || { echo "WireGuard has no peer handshake" >&2; exit 1; }
now="$(date +%s)"
fresh=1
while read -r peer timestamp; do
  case "$timestamp" in ''|0) continue ;; esac
  age=$((now - timestamp))
  [ "$age" -ge 0 ] && [ "$age" -le "$WG_MAX_HANDSHAKE_AGE_SECONDS" ] && fresh=0
done <<EOF
$handshake
EOF
[ "$fresh" -eq 0 ] || { echo "WireGuard peer handshake is stale" >&2; exit 1; }

nc -z -w 10 "$HOME_WG_IP" 25 || { echo "gateway cannot reach home Stalwart SMTP over WireGuard" >&2; exit 1; }
nc -z -w 10 "$HOME_WG_IP" 8080 || { echo "gateway cannot reach home Stalwart HTTP over WireGuard" >&2; exit 1; }
nc -z -w 10 "$HOME_WG_IP" 8010 || { echo "gateway cannot reach home identity-service over WireGuard" >&2; exit 1; }
timeout 10 bash -c 'exec 3<>/dev/tcp/smtp.resend.com/465' 2>/dev/null || { echo "smtp.resend.com:465 is unreachable" >&2; exit 1; }

direct_host="${DIRECT_MX_TEST_HOST:-gmail-smtp-in.l.google.com}"
if timeout 10 bash -c 'exec 3<>/dev/tcp/"$1"/25' bash "$direct_host" 2>/dev/null; then
  echo "direct outbound Internet TCP/25 is reachable" >&2
  exit 1
fi

firewall_rules=""
if command -v nft >/dev/null 2>&1; then firewall_rules="$(nft list ruleset 2>/dev/null || true)"; fi
if [ -z "$firewall_rules" ] && command -v iptables >/dev/null 2>&1; then firewall_rules="$(iptables -S 2>/dev/null || true)"; fi
printf '%s\n' "$firewall_rules" | grep -Eq '25|80|443' || { echo "firewall rules do not document gateway public SMTP/HTTPS ingress" >&2; exit 1; }
printf '%s\n' "$firewall_rules" | grep -Eq '51820' || { echo "firewall rules do not document WireGuard ingress" >&2; exit 1; }
printf '%s\n' "$firewall_rules" | grep -Eq '5432|8010|8080|18080|2375|2376' && { echo "firewall rules mention forbidden public management/internal ports" >&2; exit 1; } || true
printf '%s\n' "$firewall_rules" | grep -Fq "$SSH_ALLOWED_CIDRS" || { echo "SSH is not proven restricted to SSH_ALLOWED_CIDRS" >&2; exit 1; }

echo "gateway firewall, WireGuard, home-path, Resend/465, and outbound TCP/25 gates passed."
