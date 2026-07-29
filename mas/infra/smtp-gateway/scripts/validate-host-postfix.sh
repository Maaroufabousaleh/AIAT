#!/bin/sh
set -eu

base_dir="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
env_file="${1:-$base_dir/profiles/oci-e2.1-micro-host.env}"
test -f "$env_file" || { echo "missing host gateway profile" >&2; exit 1; }

env_value() { awk -F= -v key="$1" '$1 == key {sub(/^[^=]*=/, ""); sub(/\r$/, ""); print; exit}' "$env_file"; }
require_value() {
  actual="$(env_value "$1")"
  test "$actual" = "$2" || { echo "host gateway validation refused: $1 must be $2" >&2; exit 1; }
}
require_command() { command -v "$1" >/dev/null 2>&1 || { echo "$1 is required for host gateway validation" >&2; exit 1; }; }

require_value DEPLOYMENT_TOPOLOGY smtp_gateway_vps_home_stalwart_resend
require_value GATEWAY_RUNTIME host_postfix_wireguard
require_value GATEWAY_RUNTIME_PROFILE oci_e2_1_micro_host
require_value GATEWAY_CONTAINER_RUNTIME false
require_value MAIL_HOSTNAME mail.aiat.ca
require_value AGENT_MAIL_DOMAIN agents.aiat.ca
require_value HOME_WIREGUARD_IP 10.77.0.2
require_value HOME_STALWART_SMTP_PORT 2525
require_value HOST_POSTFIX_TRANSPORT_TARGET 10.77.0.2:2525
require_value PUBLIC_SMTP25_ACTIVATED false
require_value IDENTITY_DNS_MODE blocked
require_value OUTBOUND_RELAY_CERTIFIED false
require_value DIRECT_MX_OUTBOUND_ENABLED false

for command in postconf postqueue postfix wg ss ip nft nc systemctl; do
  require_command "$command"
done
postfix_main="$(env_value HOST_POSTFIX_MAIN_CF)"
transport_file="$(env_value HOST_POSTFIX_TRANSPORT_FILE)"
queue_path="$(env_value HOST_POSTFIX_QUEUE_PATH)"
wg_interface="$(env_value WIREGUARD_INTERFACE)"
test -f "$postfix_main" || { echo "host gateway validation refused: missing $postfix_main" >&2; exit 1; }
test -f "$transport_file" || { echo "host gateway validation refused: missing $transport_file" >&2; exit 1; }
test -d "$queue_path" || { echo "host gateway validation refused: missing Postfix queue path $queue_path" >&2; exit 1; }

relay_domains="$(postconf -h relay_domains 2>/dev/null || true)"
if ! printf '%s\n' "$relay_domains" | grep -Eq '(^|[[:space:],])agents\.aiat\.ca([[:space:],]|$)'; then
  relay_file="${relay_domains#*:}"
  test -f "$relay_file" && grep -Eq '^[[:space:]]*agents\.aiat\.ca([[:space:]]|$)' "$relay_file" || { echo "host gateway validation refused: relay_domains is not restricted to agents.aiat.ca" >&2; exit 1; }
fi
transport_maps="$(postconf -h transport_maps 2>/dev/null || true)"
printf '%s\n' "$transport_maps" | grep -Fq "$(basename "$transport_file")" || { echo "host gateway validation refused: transport_maps does not reference $transport_file" >&2; exit 1; }
grep -Eq '^[[:space:]]*agents\.aiat\.ca[[:space:]]+smtp:\[10\.77\.0\.2\]:2525[[:space:]]*$' "$transport_file" || { echo "host gateway validation refused: transport map target is not [10.77.0.2]:2525" >&2; exit 1; }
relay_restrictions="$(postconf -h smtpd_relay_restrictions 2>/dev/null || true)"
printf '%s\n' "$relay_restrictions" | grep -Fq reject_unauth_destination || { echo "host gateway validation refused: reject_unauth_destination is missing" >&2; exit 1; }
test -z "$(postconf -h relayhost 2>/dev/null || true)" || { echo "host gateway validation refused: relayhost must be empty" >&2; exit 1; }
! printf '%s\n' "$(postconf -h mydestination 2>/dev/null || true)" | grep -Fq agents.aiat.ca || { echo "host gateway validation refused: production domain must not be a local destination" >&2; exit 1; }

wg show "$wg_interface" >/dev/null 2>&1 || { echo "host gateway validation refused: WireGuard interface is unavailable" >&2; exit 1; }
ip -brief address show dev "$wg_interface" | grep -Eq '10\.77\.0\.1/24' || { echo "host gateway validation refused: gateway WireGuard address must be 10.77.0.1/24" >&2; exit 1; }
handshakes="$(wg show "$wg_interface" latest-handshakes 2>/dev/null || true)"
test -n "$handshakes" || { echo "host gateway validation refused: no WireGuard peer handshake" >&2; exit 1; }
now="$(date +%s)"
fresh=1
while read -r peer timestamp; do
  case "$timestamp" in ''|0) continue ;; esac
  age=$((now - timestamp))
  [ "$age" -ge 0 ] && [ "$age" -le "${WG_MAX_HANDSHAKE_AGE_SECONDS:-180}" ] && fresh=0
done <<EOF
$handshakes
EOF
[ "$fresh" -eq 0 ] || { echo "host gateway validation refused: WireGuard handshake is stale" >&2; exit 1; }

ss -ltn | grep -Eq '(^|[[:space:]])(0\.0\.0\.0|\*|\[::\]):25[[:space:]]' || { echo "host gateway validation refused: Postfix is not listening on TCP/25" >&2; exit 1; }
nc -z -w 10 "$(env_value HOME_WIREGUARD_IP)" "$(env_value HOME_STALWART_SMTP_PORT)" || { echo "host gateway validation refused: home WireGuard SMTP/2525 is unreachable" >&2; exit 1; }

systemctl is-active --quiet "wg-quick@$wg_interface" || { echo "host gateway validation refused: wg-quick service is not active" >&2; exit 1; }
systemctl is-active --quiet postfix || { echo "host gateway validation refused: host Postfix is not active" >&2; exit 1; }
queue_listing="$(postqueue -p 2>/dev/null || true)"
queue_depth="$(printf '%s\n' "$queue_listing" | awk '/^[A-F0-9]+[*!]?[[:space:]]/ {count++} END {print count + 0}')"
test -n "$queue_depth" || { echo "host gateway validation refused: queue status could not be read" >&2; exit 1; }

firewall_rules="$(nft list ruleset 2>/dev/null || true)"
printf '%s\n' "$firewall_rules" | grep -Eq '51820' || { echo "host gateway validation refused: WireGuard firewall rule is not visible" >&2; exit 1; }
if printf '%s\n' "$firewall_rules" | grep -Eq 'dport[[:space:]]+25[^\n]*(accept|dnat)'; then
  echo "host gateway validation refused: public TCP/25 appears activated before external evidence" >&2
  exit 1
fi

echo "host-level Postfix/WireGuard adoption validates; queue_depth=$queue_depth; public TCP/25 remains fail-closed."
