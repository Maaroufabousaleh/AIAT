#!/bin/sh
# Stage or activate the self-hosted production identity profile.
#
# This wrapper is the only documented production activation entry point.  The
# stage action keeps outbound disabled while live DNS, SMTP, and relay evidence
# is collected.  The activate action fails closed until every live gate passes.
set -eu

base_dir="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
env_file="${2:-$base_dir/.env.mail-edge}"
action="${1:-}"
inbound_evidence=""
delivery_evidence=""
preflight_evidence=""

usage() {
  echo "usage: $0 stage|activate ENV_FILE [--inbound-evidence FILE --delivery-evidence FILE --preflight-evidence FILE]" >&2
  exit 2
}

[ "$action" = "stage" ] || [ "$action" = "activate" ] || usage
[ -f "$env_file" ] || { echo "missing production environment file: $env_file" >&2; exit 1; }

shift 2
while [ "$#" -gt 0 ]; do
  case "$1" in
    --inbound-evidence) [ "$#" -ge 2 ] || usage; inbound_evidence="$2"; shift 2 ;;
    --delivery-evidence) [ "$#" -ge 2 ] || usage; delivery_evidence="$2"; shift 2 ;;
    --preflight-evidence) [ "$#" -ge 2 ] || usage; preflight_evidence="$2"; shift 2 ;;
    *) usage ;;
  esac
done

env_value() {
  key="$1"
  awk -F= -v key="$key" '$1 == key {sub(/^[^=]*=/, ""); sub(/\r$/, ""); print; exit}' "$env_file"
}

profile="$(env_value IDENTITY_PROFILE)"
environment="$(env_value MAS_ENVIRONMENT)"
topology="$(env_value DEPLOYMENT_TOPOLOGY)"
agent_domain="$(env_value AGENT_MAIL_DOMAIN)"
mail_hostname="$(env_value MAIL_HOSTNAME)"
identity_hostname="$(env_value IDENTITY_HOSTNAME)"
certified="$(env_value OUTBOUND_RELAY_CERTIFIED)"
default_outbound="$(env_value DEFAULT_OUTBOUND_ENABLED)"

[ "$profile" = "production" ] || { echo "IDENTITY_PROFILE must be production" >&2; exit 1; }
[ "$environment" = "production" ] || { echo "MAS_ENVIRONMENT must be production" >&2; exit 1; }
[ "$topology" = "self_hosted_stalwart_resend" ] || { echo "DEPLOYMENT_TOPOLOGY must be self_hosted_stalwart_resend" >&2; exit 1; }
[ "$agent_domain" = "agents.aiat.ca" ] || { echo "AGENT_MAIL_DOMAIN must be agents.aiat.ca" >&2; exit 1; }
[ "$mail_hostname" = "mail.aiat.ca" ] || { echo "MAIL_HOSTNAME must be mail.aiat.ca" >&2; exit 1; }
[ "$identity_hostname" = "identity.aiat.ca" ] || { echo "IDENTITY_HOSTNAME must be identity.aiat.ca" >&2; exit 1; }
[ "$default_outbound" = "false" ] || { echo "DEFAULT_OUTBOUND_ENABLED must remain false" >&2; exit 1; }

run_static_checks() {
  sh "$base_dir/scripts/validate-mail-edge.sh" "$env_file"
}

run_live_checks() {
  public_ip="$(env_value PUBLIC_MAIL_IP)"
  [ -n "$public_ip" ] || { echo "PUBLIC_MAIL_IP is required" >&2; exit 1; }
  PRIMARY_DOMAIN="$(env_value PRIMARY_DOMAIN)" \
  AGENT_MAIL_DOMAIN="$agent_domain" \
  MAIL_HOSTNAME="$mail_hostname" \
  IDENTITY_HOSTNAME="$(env_value IDENTITY_HOSTNAME)" \
  PUBLIC_MAIL_IP="$public_ip" \
  DKIM_SELECTOR="$(env_value DKIM_SELECTOR)" \
  RESEND_RETURN_PATH_SUBDOMAIN="$(env_value RESEND_RETURN_PATH_SUBDOMAIN)" \
  RESEND_BOUNCE_MX_HOST="$(env_value RESEND_BOUNCE_MX_HOST)" \
    sh "$base_dir/scripts/validate-dns.sh"
  sh "$base_dir/scripts/validate-firewall.sh"
  MAIL_HOSTNAME="$mail_hostname" IDENTITY_HOSTNAME="$(env_value IDENTITY_HOSTNAME)" \
    sh "$base_dir/scripts/validate-tls.sh"
  OUTBOUND_RELAY_HOST="$(env_value OUTBOUND_RELAY_HOST)" \
  OUTBOUND_RELAY_PORT="$(env_value OUTBOUND_RELAY_PORT)" \
    sh "$base_dir/scripts/validate-smtp-relay.sh"
}

run_self_hosted_preflight() {
  [ -n "$preflight_evidence" ] && [ -s "$preflight_evidence" ] || {
    echo "refusing activation: external self-hosted preflight evidence is required" >&2
    exit 1
  }
  grep -q '^AIAT_SELF_HOSTED_PREFLIGHT=PASS$' "$preflight_evidence" || {
    echo "refusing activation: external self-hosted preflight evidence is not a PASS record" >&2
    exit 1
  }
  sh "$base_dir/scripts/preflight-self-hosted.sh" "$env_file" \
    --external-target "$mail_hostname" \
    --external-evidence "$preflight_evidence" \
    --allow-certified
}

if [ "$action" = "stage" ]; then
  run_static_checks
  [ "$certified" = "false" ] || {
    echo "staging must start with OUTBOUND_RELAY_CERTIFIED=false" >&2
    exit 1
  }
  docker compose --env-file "$env_file" up -d identity-postgres stalwart
  docker compose --env-file "$env_file" run --rm identity-migrate
  docker compose --env-file "$env_file" up -d identity-service ingress
  echo "Production mail edge staged with outbound disabled; collect live evidence before activation."
  exit 0
fi

[ "$certified" = "true" ] || {
  echo "refusing activation: Resend relay certification is not recorded" >&2
  exit 1
}
[ -n "$inbound_evidence" ] && [ -s "$inbound_evidence" ] || {
  echo "refusing activation: external inbound SMTP evidence is required" >&2
  exit 1
}
[ -n "$delivery_evidence" ] && [ -s "$delivery_evidence" ] || {
  echo "refusing activation: external delivery evidence is required" >&2
  exit 1
}
grep -q '^AIAT_INBOUND_SMTP_TEST=PASS$' "$inbound_evidence" || {
  echo "refusing activation: inbound evidence is not a PASS record" >&2
  exit 1
}
grep -q '^AIAT_EXTERNAL_DELIVERY_TEST=PASS$' "$delivery_evidence" || {
  echo "refusing activation: delivery evidence is not a PASS record" >&2
  exit 1
}

run_static_checks
run_self_hosted_preflight
run_live_checks
docker compose --env-file "$env_file" up -d identity-service ingress
echo "Production identity profile activated after DNS, MX, TLS, SMTP, firewall, port-forwarding evidence, and external delivery certification."
