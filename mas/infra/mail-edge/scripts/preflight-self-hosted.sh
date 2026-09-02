#!/bin/sh
# Fail-closed self-hosting preflight for the production Stalwart profile.
#
# Run this from the self-hosted machine after staging, and repeat the public
# reachability portion from a network that is outside the machine's router or
# firewall. The activation wrapper requires the externally collected marker.
set -eu

base_dir="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
env_file="${1:-$base_dir/.env.mail-edge}"
allow_certified=false
external_target=""
external_evidence=""
external_only=false

usage() {
  echo "usage: $0 ENV_FILE --external-target HOST [--external-only] [--external-evidence FILE] [--allow-certified]" >&2
  exit 2
}

[ -f "$env_file" ] || { echo "self-hosted preflight: missing environment file: $env_file" >&2; exit 1; }
if [ "$#" -gt 0 ]; then shift; fi
while [ "$#" -gt 0 ]; do
  case "$1" in
    --external-target) [ "$#" -ge 2 ] || usage; external_target="$2"; shift 2 ;;
    --external-only) external_only=true; shift ;;
    --external-evidence) [ "$#" -ge 2 ] || usage; external_evidence="$2"; shift 2 ;;
    --allow-certified) allow_certified=true; shift ;;
    *) usage ;;
  esac
done
[ -n "$external_target" ] || { echo "self-hosted preflight: --external-target is required; public reachability must be tested by hostname" >&2; exit 1; }

env_value() {
  key="$1"
  awk -F= -v key="$key" '$1 == key {sub(/^[^=]*=/, ""); sub(/\r$/, ""); print; exit}' "$env_file"
}

require_value() {
  key="$1"
  expected="$2"
  actual="$(env_value "$key")"
  [ "$actual" = "$expected" ] || {
    echo "self-hosted preflight: $key must be $expected" >&2
    exit 1
  }
}

is_public_ipv4() {
  awk -F. '
    BEGIN { valid = 1 }
    NF != 4 { valid = 0 }
    {
      for (i = 1; i <= 4; i++) {
        if ($i !~ /^[0-9]+$/ || $i < 0 || $i > 255) valid = 0
      }
      if ($1 == 0 || $1 == 10 || $1 == 127 || $1 >= 224) valid = 0
      if ($1 == 100 && $2 >= 64 && $2 <= 127) valid = 0
      if ($1 == 169 && $2 == 254) valid = 0
      if ($1 == 172 && $2 >= 16 && $2 <= 31) valid = 0
      if ($1 == 192 && $2 == 168) valid = 0
      if ($1 == 192 && $2 == 0 && $3 == 2) valid = 0
      if ($1 == 198 && $2 == 51 && $3 == 100) valid = 0
      if ($1 == 203 && $2 == 0 && $3 == 113) valid = 0
    }
    END { exit valid ? 0 : 1 }
  ' <<EOF
$(env_value PUBLIC_MAIL_IP)
EOF
}

has_non_loopback_listener() {
  port="$1"
  ss -H -ltn | awk -v suffix=":$port" '
    $4 ~ suffix "$" {
      address = $4
      sub(suffix "$", "", address)
      gsub(/^\[/, "", address)
      gsub(/\]$/, "", address)
      if (address != "127.0.0.1" && address != "::1" && address != "localhost") bad = 1
    }
    END { exit bad ? 0 : 1 }
  '
}

check_open() {
  host="$1"
  port="$2"
  nc -z -w 10 "$host" "$port" >/dev/null 2>&1
}

check_closed() {
  host="$1"
  port="$2"
  ! check_open "$host" "$port"
}

require_value DEPLOYMENT_TOPOLOGY self_hosted_stalwart_resend
require_value MAS_ENVIRONMENT production
require_value IDENTITY_PROFILE production
require_value PRIMARY_DOMAIN aiat.ca
require_value AGENT_MAIL_DOMAIN agents.aiat.ca
require_value MAIL_HOSTNAME mail.aiat.ca
require_value IDENTITY_HOSTNAME identity.aiat.ca
require_value DEFAULT_OUTBOUND_ENABLED false
require_value DIRECT_MX_OUTBOUND_ENABLED false

public_ip="$(env_value PUBLIC_MAIL_IP)"
[ -n "$public_ip" ] && is_public_ipv4 || {
  echo "self-hosted preflight: PUBLIC_MAIL_IP must be a real public IPv4; private, documentation, and RFC6598 CGNAT addresses are rejected" >&2
  exit 1
}

certified="$(env_value OUTBOUND_RELAY_CERTIFIED)"
if [ "$allow_certified" = true ]; then
  [ "$certified" = true ] || {
    echo "self-hosted preflight: --allow-certified requires OUTBOUND_RELAY_CERTIFIED=true after live relay certification" >&2
    exit 1
  }
  [ -n "$external_evidence" ] && [ -s "$external_evidence" ] || {
    echo "self-hosted preflight: certified activation requires external preflight evidence" >&2
    exit 1
  }
  grep -q '^AIAT_SELF_HOSTED_PREFLIGHT=PASS$' "$external_evidence" || {
    echo "self-hosted preflight: external preflight evidence is not a PASS record" >&2
    exit 1
  }
else
  [ "$certified" = false ] || {
    echo "self-hosted preflight: OUTBOUND_RELAY_CERTIFIED must remain false until live Resend relay certification" >&2
    exit 1
  }
fi

for command in awk nc dig curl; do
  command -v "$command" >/dev/null 2>&1 || {
    echo "self-hosted preflight: required command is missing: $command" >&2
    exit 1
  }
done

if [ "$external_only" = false ]; then
  command -v ss >/dev/null 2>&1 || {
    echo "self-hosted preflight: required command is missing: ss" >&2
    exit 1
  }

  for restricted_port in 5432 8010 18080 2525 3000 4000 4001 5173 8000 8001 8002 8003 8011 8080 9000 9001 9090 20128; do
    if has_non_loopback_listener "$restricted_port"; then
      echo "self-hosted preflight: restricted or development port $restricted_port is bound beyond loopback" >&2
      exit 1
    fi
  done

  ss -H -ltn | grep -Eq '(:25|:80|:443)$' || {
    echo "self-hosted preflight: local SMTP/HTTP/HTTPS listeners are incomplete" >&2
    exit 1
  }

  resend_host="$(env_value OUTBOUND_RELAY_HOST)"
  resend_port="$(env_value OUTBOUND_RELAY_PORT)"
  [ "$resend_host" = smtp.resend.com ] && [ "$resend_port" = 465 ] || {
    echo "self-hosted preflight: outbound relay must be smtp.resend.com:465" >&2
    exit 1
  }
  check_open "$resend_host" "$resend_port" || {
    echo "self-hosted preflight: outbound smtp.resend.com:465 is unreachable" >&2
    exit 1
  }

  direct_mx_host="$(env_value DIRECT_MX_TEST_HOST)"
  [ -n "$direct_mx_host" ] || direct_mx_host=gmail-smtp-in.l.google.com
  check_closed "$direct_mx_host" 25 || {
    echo "self-hosted preflight: direct outbound TCP 25 is reachable and must be blocked" >&2
    exit 1
  }

  firewall_rule=false
  if command -v iptables >/dev/null 2>&1 && iptables -S OUTPUT 2>/dev/null | grep -Eq -- '--dport 25.*(REJECT|DROP)'; then
    firewall_rule=true
  fi
  if command -v nft >/dev/null 2>&1 && nft list ruleset 2>/dev/null | grep -Eiq 'tcp[[:space:]]+dport[[:space:]]+25.*(drop|reject)'; then
    firewall_rule=true
  fi
  if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -Eiq '25/tcp.*DENY.*OUT'; then
    firewall_rule=true
  fi
  [ "$firewall_rule" = true ] || {
    echo "self-hosted preflight: no explicit host-firewall rule blocks outbound TCP 25" >&2
    exit 1
  }
fi

for public_name in "$external_target" "$(env_value IDENTITY_HOSTNAME)"; do
  resolved_ip="$(dig +short A "$public_name" | head -n 1 | sed 's/[.]$//')"
  [ "$resolved_ip" = "$public_ip" ] || {
    echo "self-hosted preflight: $public_name must resolve to PUBLIC_MAIL_IP=$public_ip" >&2
    exit 1
  }
done

check_open "$external_target" 25 || {
  echo "self-hosted preflight: inbound SMTP TCP 25 is not reachable from the external probe network (possible unsupported CGNAT, router, or firewall block)" >&2
  exit 1
}
for public_name in "$external_target" "$(env_value IDENTITY_HOSTNAME)"; do
  for public_port in 80 443; do
    check_open "$public_name" "$public_port" || {
      echo "self-hosted preflight: inbound TCP $public_port is not reachable at $public_name" >&2
      exit 1
    }
  done
done

for restricted_port in 5432 8010 18080 2525 3000 4000 4001 5173 8000 8001 8002 8003 8011 8080 9000 9001 9090 20128; do
  check_closed "$external_target" "$restricted_port" || {
    echo "self-hosted preflight: restricted or development port $restricted_port is publicly reachable" >&2
    exit 1
  }
done

for admin_path in /admin /api; do
  status="$(curl --silent --show-error --output /dev/null --write-out '%{http_code}' --max-time 15 "https://$(env_value MAIL_HOSTNAME)$admin_path")" || {
    echo "self-hosted preflight: public TLS check for Stalwart management path $admin_path failed" >&2
    exit 1
  }
  [ "$status" = 404 ] || {
    echo "self-hosted preflight: Stalwart management path $admin_path is publicly exposed (HTTP $status)" >&2
    exit 1
  }
done

echo "self-hosted preflight passed: public IPv4, non-CGNAT reachability, inbound 25/80/443, Resend 465, outbound 25 denial, and restricted-port closure are verified."
