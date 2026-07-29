#!/bin/sh
set -eu

base_dir="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
env_file="${1:-$base_dir/profiles/oci-e2.1-micro-host.env}"
output=""
shift || true
while [ "$#" -gt 0 ]; do
  case "$1" in
    --output) output="${2:?--output needs a file}"; shift 2 ;;
    *) echo "unknown evidence option: $1" >&2; exit 2 ;;
  esac
done
test -f "$env_file" || { echo "missing host gateway profile" >&2; exit 1; }
env_value() { awk -F= -v key="$1" '$1 == key {sub(/^[^=]*=/, ""); sub(/\r$/, ""); print; exit}' "$env_file"; }
output="${output:-$(env_value GATEWAY_HOST_EVIDENCE)}"
test -n "$output" || { echo "evidence output path is required" >&2; exit 1; }
for command in postconf postqueue wg ss ip nft systemctl; do
  command -v "$command" >/dev/null 2>&1 || { echo "$command is required for sanitized evidence" >&2; exit 1; }
done

mkdir -p "$(dirname "$output")"
umask 077
tmp="${output}.tmp.$$"
trap 'rm -f "$tmp"' EXIT INT TERM
sanitize() {
  sed -E \
    -e 's/[[:alnum:]._%+-]+@[[:alnum:].-]+/[redacted-email]/g' \
    -e 's/[A-Za-z0-9+\/=]{32,}/[redacted-token]/g' \
    -e 's/([0-9]{1,3}\.){3}[0-9]{1,3}/[redacted-ip]/g'
}
queue_listing="$(postqueue -p 2>/dev/null || true)"
queue_depth="$(printf '%s\n' "$queue_listing" | awk '/^[A-F0-9]+[*!]?[[:space:]]/ {count++} END {print count + 0}')"
transport_file="$(env_value HOST_POSTFIX_TRANSPORT_FILE)"
transport_target=FAIL
grep -Eq '^[[:space:]]*agents\.aiat\.ca[[:space:]]+smtp:\[10\.77\.0\.2\]:2525[[:space:]]*$' "$transport_file" && transport_target=PASS || true
wg_interface="$(env_value WIREGUARD_INTERFACE)"
handshakes="$(wg show "$wg_interface" latest-handshakes 2>/dev/null || true)"
wg_handshake=FAIL
printf '%s\n' "$handshakes" | awk '$2 > 0 {found=1} END {exit(found ? 0 : 1)}' && wg_handshake=PASS || true
firewall_rules="$(nft list ruleset 2>/dev/null || true)"
wg_firewall=FAIL
printf '%s\n' "$firewall_rules" | grep -Eq '51820' && wg_firewall=PASS || true
public25=BLOCKED
printf '%s\n' "$firewall_rules" | grep -Eq 'dport[[:space:]]+25[^\n]*(accept|dnat)' && public25=REVIEW_REQUIRED || true
postfix_listener=FAIL
ss -ltn | grep -Eq '(^|[[:space:]])(0\.0\.0\.0|\*|\[::\]):25[[:space:]]' && postfix_listener=PASS || true
home_relay=FAIL
nc -z -w 10 "$(env_value HOME_WIREGUARD_IP)" "$(env_value HOME_STALWART_SMTP_PORT)" >/dev/null 2>&1 && home_relay=PASS || true

{
  echo "EVIDENCE_VERSION=aiat-smtp-gateway-host-v1"
  echo "EVIDENCE_SCOPE=sanitized_read_only_host_observation"
  echo "COLLECTED_AT_UTC=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "GATEWAY_RUNTIME=$(env_value GATEWAY_RUNTIME)"
  echo "GATEWAY_RUNTIME_PROFILE=$(env_value GATEWAY_RUNTIME_PROFILE)"
  echo "HOST_OS=$(grep '^PRETTY_NAME=' /etc/os-release 2>/dev/null | cut -d= -f2- | tr -d '"' | sanitize)"
  echo "HOST_CPU_COUNT=$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo unknown)"
  echo "HOST_MEMORY_MB=$(awk '/MemTotal:/ {printf "%d", $2 / 1024}' /proc/meminfo 2>/dev/null || echo unknown)"
  echo "HOST_SWAP_MB=$(awk '/SwapTotal:/ {printf "%d", $2 / 1024}' /proc/meminfo 2>/dev/null || echo unknown)"
  echo "POSTFIX_ACTIVE=$(systemctl is-active postfix 2>/dev/null || true)"
  echo "POSTFIX_RELAY_DOMAINS=$(postconf -h relay_domains 2>/dev/null | sanitize)"
  echo "POSTFIX_TRANSPORT_MAPS=$(postconf -h transport_maps 2>/dev/null | sed 's#/[[:alnum:]_.-]*/#/[redacted-path]/#g' | sanitize)"
  echo "POSTFIX_TRANSPORT_TARGET_10_77_0_2_2525=$transport_target"
  echo "POSTFIX_RELAY_RESTRICTION=$(postconf -h smtpd_relay_restrictions 2>/dev/null | grep -Fq reject_unauth_destination && echo PASS || echo FAIL)"
  echo "POSTFIX_RELAYHOST_EMPTY=$(test -z "$(postconf -h relayhost 2>/dev/null || true)" && echo PASS || echo FAIL)"
  echo "POSTFIX_LISTEN_TCP25=$postfix_listener"
  echo "WIREGUARD_SERVICE=$(systemctl is-active "wg-quick@$wg_interface" 2>/dev/null || true)"
  echo "WIREGUARD_ADDRESS=$(ip -brief address show dev "$wg_interface" 2>/dev/null | grep -o '10\.77\.0\.1/[0-9]*' | head -1 || true)"
  echo "WIREGUARD_HANDSHAKE=$wg_handshake"
  echo "WIREGUARD_FIREWALL_51820=$wg_firewall"
  echo "HOME_RELAY_10_77_0_2_2525=$home_relay"
  echo "POSTFIX_QUEUE_DEPTH=$queue_depth"
  echo "POSTFIX_QUEUE_PATH_EXISTS=$(test -d "$(env_value HOST_POSTFIX_QUEUE_PATH)" && echo PASS || echo FAIL)"
  echo "POSTFIX_QUEUE_MUTATION=NOT_PERFORMED"
  echo "WIREGUARD_KEY_MUTATION=NOT_PERFORMED"
  echo "PUBLIC_TCP25_STATE=$public25"
  echo "IDENTITY_DNS_MODE=$(env_value IDENTITY_DNS_MODE)"
  echo "IDENTITY_HTTPS_INGRESS_CERTIFIED=$(env_value IDENTITY_HTTPS_INGRESS_CERTIFIED)"
  echo "RESEND_OUTBOUND_RELAY_CERTIFIED=$(env_value OUTBOUND_RELAY_CERTIFIED)"
  echo "SYSTEMD_RELEVANT_UNITS=$(systemctl list-units --type=service --all --no-legend 2>/dev/null | awk '$1 ~ /(postfix|wg-quick|socat)/ {print $1 ":" $3}' | sanitize | tr '\n' ';')"
} >"$tmp"
mv -f "$tmp" "$output"
trap - EXIT INT TERM
chmod 600 "$output"
echo "sanitized host evidence written to $output; no Postfix queue, WireGuard key, DNS, firewall, or service mutation was performed."
