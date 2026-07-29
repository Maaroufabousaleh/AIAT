#!/bin/sh
set -eu

base_dir="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
env_file="${1:-$base_dir/profiles/oci-e2.1-micro-host.env}"
gate="${2:-all}"
test -f "$env_file" || { echo "missing host gateway profile" >&2; exit 1; }
env_value() { awk -F= -v key="$1" '$1 == key {sub(/^[^=]*=/, ""); sub(/\r$/, ""); print; exit}' "$env_file"; }
marker() {
  file="$1"; key="$2"
  test -f "$file" || { echo "gate refused: missing evidence file $file" >&2; exit 1; }
  grep -Eq "^${key}=PASS$" "$file" || { echo "gate refused: ${key}=PASS is missing from $file" >&2; exit 1; }
}
require_command() { command -v "$1" >/dev/null 2>&1 || { echo "$1 is required for gate $gate" >&2; exit 1; }; }
require_value() {
  test "$(env_value "$1")" = "$2" || { echo "gate refused: $1 must be $2" >&2; exit 1; }
}

require_value DEPLOYMENT_TOPOLOGY smtp_gateway_vps_home_stalwart_resend
require_value GATEWAY_RUNTIME host_postfix_wireguard
require_value GATEWAY_RUNTIME_PROFILE oci_e2_1_micro_host
case "$gate" in
  pre-activation|internal-relay|external-inbound|dns-mx|identity-https|resend|all) ;;
  *) echo "usage: $0 PROFILE [pre-activation|internal-relay|external-inbound|dns-mx|identity-https|resend|all]" >&2; exit 2 ;;
esac

check_pre_activation() {
  require_value PUBLIC_SMTP25_ACTIVATED false
  require_value IDENTITY_DNS_MODE blocked
  require_value IDENTITY_HTTPS_INGRESS_CERTIFIED false
  require_value OUTBOUND_RELAY_CERTIFIED false
  "$base_dir/scripts/validate-host-postfix.sh" "$env_file"
  require_command nft
  firewall_rules="$(nft list ruleset 2>/dev/null || true)"
  if printf '%s\n' "$firewall_rules" | grep -Eq 'dport[[:space:]]+25[^\n]*(accept|dnat)'; then
    echo "pre-activation gate refused: public TCP/25 appears activated before external evidence" >&2
    exit 1
  fi
}
check_internal() {
  case "${1:-strict}" in
    allow-certified-state)
      "$base_dir/scripts/validate-host-postfix.sh" "$env_file" --allow-identity-state --allow-resend-state
      ;;
    strict)
      "$base_dir/scripts/validate-host-postfix.sh" "$env_file"
      ;;
    *) echo "internal-relay gate refused: invalid host validation mode" >&2; exit 2 ;;
  esac
  marker "$(env_value GATE_INTERNAL_RELAY_EVIDENCE)" GATEWAY_INTERNAL_RELAY_CERTIFIED
}
check_external() {
  require_command nc
  require_value PUBLIC_SMTP25_ACTIVATED true
  target="$(env_value SMTP_GATEWAY_PUBLIC_IP)"
  nc -z -w 15 "$target" 25 || { echo "external-inbound gate refused: public TCP/25 is not reachable" >&2; exit 1; }
  marker "$(env_value GATE_EXTERNAL_INBOUND_EVIDENCE)" EXTERNAL_INBOUND_SMTP_CERTIFIED
}
check_dns() {
  require_command dig
  public_ip="$(env_value SMTP_GATEWAY_PUBLIC_IP)"
  test "$public_ip" != "<public IPv4 of the gateway VPS>" || { echo "dns-mx gate refused: public gateway IPv4 is not configured" >&2; exit 1; }
  dig +short A "$(env_value MAIL_HOSTNAME)" | grep -Fx "$public_ip" >/dev/null || { echo "dns-mx gate refused: mail A record is not the gateway" >&2; exit 1; }
  dig +short MX "$(env_value AGENT_MAIL_DOMAIN)" | awk '$1 == "10" && $2 == "mail.aiat.ca." {found=1} END {exit(found ? 0 : 1)}' || { echo "dns-mx gate refused: agents MX is not mail.aiat.ca priority 10" >&2; exit 1; }
  if [ "$(env_value IDENTITY_DNS_MODE)" = blocked ]; then
    test -z "$(dig +short A "$(env_value IDENTITY_HOSTNAME)")" || { echo "dns-mx gate refused: identity DNS must remain absent while HTTPS ingress is blocked" >&2; exit 1; }
  fi
  marker "$(env_value GATE_DNS_MX_EVIDENCE)" DNS_MX_CERTIFIED
}
check_identity() {
  require_command curl
  require_value IDENTITY_DNS_MODE gateway_reverse_proxy
  require_value IDENTITY_HTTPS_INGRESS_CERTIFIED true
  code="$(curl -sS -o /dev/null -w '%{http_code}' --connect-timeout 15 --max-time 20 "https://$(env_value IDENTITY_HOSTNAME)/readyz")" || { echo "identity-https gate refused: HTTPS ingress is unreachable" >&2; exit 1; }
  test "$code" != 000 || { echo "identity-https gate refused: TLS did not complete" >&2; exit 1; }
  marker "$(env_value GATE_IDENTITY_HTTPS_EVIDENCE)" HTTPS_IDENTITY_INGRESS_CERTIFIED
}
check_resend() {
  require_command nc
  nc -z -w 15 smtp.resend.com 465 || { echo "resend gate refused: smtp.resend.com:465 is unreachable" >&2; exit 1; }
  marker "$(env_value GATE_RESEND_EVIDENCE)" RESEND_OUTBOUND_RELAY_CERTIFIED
}

case "$gate" in
  pre-activation) check_pre_activation ;;
  internal-relay) check_internal ;;
  external-inbound) check_external ;;
  dns-mx) check_dns ;;
  identity-https) check_identity ;;
  resend) check_resend ;;
  all)
    check_internal allow-certified-state
    check_external
    check_dns
    check_identity
    check_resend
    echo "all five host gateway certification gates passed; no activation or configuration mutation was performed."
    exit 0
    ;;
esac
echo "$gate gate passed; no activation or configuration mutation was performed."
