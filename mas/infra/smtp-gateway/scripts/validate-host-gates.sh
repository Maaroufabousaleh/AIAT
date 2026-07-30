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
evidence_value() {
  file="$1"; key="$2"
  awk -F= -v key="$key" '$1 == key {sub(/^[^=]*=/, ""); sub(/\r$/, ""); print; exit}' "$file"
}
require_evidence_value() {
  file="$1"; key="$2"; expected="$3"
  actual="$(evidence_value "$file" "$key")"
  test "$actual" = "$expected" || {
    echo "$gate gate refused: $key must be $expected in $file" >&2
    exit 1
  }
}
require_evidence_pattern() {
  file="$1"; key="$2"; pattern="$3"
  actual="$(evidence_value "$file" "$key")"
  printf '%s\n' "$actual" | grep -Eq "$pattern" || {
    echo "$gate gate refused: malformed or missing $key in $file" >&2
    exit 1
  }
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
validate_external_evidence() {
  evidence_file="$(env_value GATE_EXTERNAL_INBOUND_EVIDENCE)"
  marker "$evidence_file" EXTERNAL_INBOUND_SMTP_CERTIFIED

  source_ip="$(evidence_value "$evidence_file" EXTERNAL_SOURCE_IP)"
  probe_origin="$(evidence_value "$evidence_file" EXTERNAL_PROBE_ORIGIN)"
  if [ -z "$source_ip" ] && [ -z "$probe_origin" ]; then
    echo "external-inbound gate refused: external source IP or probe origin is required in $evidence_file" >&2
    exit 1
  fi
  if [ -n "$source_ip" ]; then
    printf '%s\n' "$source_ip" | grep -Eq '^(([0-9]{1,3}\.){3}[0-9]{1,3}|[0-9A-Fa-f:]+)$' || {
      echo "external-inbound gate refused: malformed EXTERNAL_SOURCE_IP in $evidence_file" >&2
      exit 1
    }
  fi
  if [ -n "$probe_origin" ]; then
    printf '%s\n' "$probe_origin" | grep -Eq '^[[:print:]]+$' || {
      echo "external-inbound gate refused: malformed EXTERNAL_PROBE_ORIGIN in $evidence_file" >&2
      exit 1
    }
  fi
  require_evidence_value "$evidence_file" DESTINATION_HOSTNAME "$(env_value MAIL_HOSTNAME)"
  require_evidence_value "$evidence_file" DESTINATION_TCP_PORT 25
  require_evidence_pattern "$evidence_file" SMTP_ACCEPTANCE '^250[[:space:]]+2\.0\.0([[:space:]].*)?$'
  require_evidence_pattern "$evidence_file" PRODUCTION_RECIPIENT '^[^[:space:]@]+@agents\.aiat\.ca$'
  require_evidence_pattern "$evidence_file" POSTFIX_QUEUE_ID '^[[:alnum:]]{5,}$'
  require_evidence_value "$evidence_file" DOWNSTREAM_RELAY_TARGET 10.77.0.2:2525
  require_evidence_value "$evidence_file" FINAL_STATUS sent
}
informational_self_probe() {
  target="$(env_value SMTP_GATEWAY_PUBLIC_IP)"
  case "$target" in
    ''|'<public IPv4 of the gateway VPS>')
      echo "external-inbound informational: local self-probe skipped; public IPv4 is not configured" ;;
    *)
      if command -v nc >/dev/null 2>&1; then
        if nc -z -w 5 "$target" 25 >/dev/null 2>&1; then
          echo "external-inbound informational: gateway self-probe succeeded; external evidence remains authoritative" ;
        else
          echo "external-inbound informational: gateway self-probe failed; this may reflect unavailable public-IP hairpin/NAT reflection; external evidence remains authoritative" ;
        fi
      else
        echo "external-inbound informational: local self-probe skipped; nc is unavailable" ;
      fi
      ;;
  esac
}
check_external() {
  require_value PUBLIC_SMTP25_ACTIVATED true
  validate_external_evidence
  informational_self_probe
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
  require_command openssl
  require_command timeout
  require_value DEFAULT_OUTBOUND_ENABLED false
  require_value DIRECT_MX_OUTBOUND_ENABLED false
  require_value OUTBOUND_RELAY_CERTIFIED false
  nc -z -w 15 smtp.resend.com 465 || { echo "resend gate refused: smtp.resend.com:465 TCP reachability failed" >&2; exit 1; }
  tls_output="$(timeout 20 openssl s_client -connect smtp.resend.com:465 -servername smtp.resend.com -verify_return_error -brief </dev/null 2>&1 || true)"
  printf '%s\n' "$tls_output" | grep -Eq 'Verification:[[:space:]]+OK|Verify return code:[[:space:]]+0[[:space:]]+\(ok\)' || {
    echo "resend gate refused: smtp.resend.com:465 TLS certificate verification failed" >&2
    exit 1
  }
  evidence_file="$(env_value GATE_RESEND_EVIDENCE)"
  marker "$evidence_file" RESEND_OUTBOUND_RELAY_CERTIFIED
  if grep -Eq '^RESEND_API_KEY=' "$evidence_file"; then
    echo "resend gate refused: RESEND_API_KEY must never be stored in evidence" >&2
    exit 1
  fi
  require_evidence_value "$evidence_file" RELAY_HOST smtp.resend.com
  require_evidence_value "$evidence_file" RELAY_PORT 465
  require_evidence_value "$evidence_file" TLS_MODE implicit
  require_evidence_value "$evidence_file" TLS_VERIFICATION PASS
  require_evidence_value "$evidence_file" SMTP_AUTHENTICATION PASS
  require_evidence_value "$evidence_file" AUTH_USERNAME resend
  require_evidence_pattern "$evidence_file" PRODUCTION_SENDER '^[^[:space:]@]+@agents\.aiat\.ca$'
  external_recipient="$(evidence_value "$evidence_file" EXTERNAL_RECIPIENT)"
  printf '%s\n' "$external_recipient" | grep -Eq '^[^[:space:]@]+@[^[:space:]@]+$' || {
    echo "resend gate refused: malformed or missing EXTERNAL_RECIPIENT in $evidence_file" >&2
    exit 1
  }
  case "$external_recipient" in
    *@agents.aiat.ca) echo "resend gate refused: EXTERNAL_RECIPIENT must be external to agents.aiat.ca" >&2; exit 1 ;;
  esac
  require_evidence_value "$evidence_file" STALWART_ROUTE resend-relay
  require_evidence_value "$evidence_file" DIRECT_MX_OUTBOUND_ENABLED false
  require_evidence_pattern "$evidence_file" STALWART_SUBMISSION_ID '^[[:print:]]{5,}$'
  resend_provider_message_id="$(evidence_value "$evidence_file" RESEND_PROVIDER_MESSAGE_ID)"
  printf '%s\n' "$resend_provider_message_id" | grep -Eq '^[[:print:]]{5,}$' || {
    echo "resend gate refused: missing actual RESEND_PROVIDER_MESSAGE_ID in $evidence_file" >&2
    exit 1
  }
  stalwart_submission_id="$(evidence_value "$evidence_file" STALWART_SUBMISSION_ID)"
  test "$resend_provider_message_id" != "$stalwart_submission_id" || {
    echo "resend gate refused: RESEND_PROVIDER_MESSAGE_ID must not be the local STALWART_SUBMISSION_ID" >&2
    exit 1
  }
  require_evidence_pattern "$evidence_file" ORIGINAL_MESSAGE_ID '^<[^[:space:]<>]+>$'
  require_evidence_pattern "$evidence_file" EXTERNAL_RECEIPT_ID '^[[:print:]]{5,}$'
  require_evidence_value "$evidence_file" DELIVERY_STATUS delivered
  require_evidence_value "$evidence_file" REPLY_RECEIVED PASS
  require_evidence_pattern "$evidence_file" REPLY_MESSAGE_ID '^<[^[:space:]<>]+>$'
  require_evidence_value "$evidence_file" REPLY_IN_REPLY_TO "$(evidence_value "$evidence_file" ORIGINAL_MESSAGE_ID)"
  require_evidence_value "$evidence_file" REPLY_TOKEN_VERIFIED PASS
  require_evidence_pattern "$evidence_file" CERTIFIED_AT '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?(Z|[+-][0-9]{2}:[0-9]{2})$'
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
