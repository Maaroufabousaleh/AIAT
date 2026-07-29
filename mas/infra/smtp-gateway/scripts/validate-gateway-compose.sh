#!/bin/sh
set -eu

base_dir="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
env_file="${1:-$base_dir/.env.smtp-gateway}"
test -f "$env_file" || { echo "missing SMTP gateway environment file" >&2; exit 1; }
command -v jq >/dev/null 2>&1 || { echo "jq is required" >&2; exit 1; }
command -v docker >/dev/null 2>&1 || { echo "docker is required" >&2; exit 1; }

env_value() {
  awk -F= -v key="$1" '$1 == key {sub(/^[^=]*=/, ""); sub(/\r$/, ""); print; exit}' "$env_file"
}
require_value() {
  actual="$(env_value "$1")"
  test "$actual" = "$2" || { echo "$1 must be $2" >&2; exit 1; }
}

require_value DEPLOYMENT_TOPOLOGY smtp_gateway_vps_home_stalwart_resend
require_value MAS_ENVIRONMENT production
require_value IDENTITY_PROFILE production
require_value AGENT_MAIL_DOMAIN agents.aiat.ca
require_value MAIL_HOSTNAME mail.aiat.ca
require_value IDENTITY_HOSTNAME identity.aiat.ca
require_value DIRECT_MX_OUTBOUND_ENABLED false
require_value DEFAULT_OUTBOUND_ENABLED false
require_value OUTBOUND_RELAY_CERTIFIED false
require_value OUTBOUND_RELAY_HOST smtp.resend.com
require_value OUTBOUND_RELAY_PORT 465
require_value OUTBOUND_RELAY_TLS_MODE implicit
test "$(env_value PUBLIC_MAIL_IP)" = "$(env_value SMTP_GATEWAY_PUBLIC_IP)" || { echo "PUBLIC_MAIL_IP and SMTP_GATEWAY_PUBLIC_IP must identify the same gateway IPv4" >&2; exit 1; }

rendered_json="$(docker compose --env-file "$env_file" -f "$base_dir/docker-compose.yml" config --format json)"
printf '%s\n' "$rendered_json" | jq -e '.services["postfix-gateway"].image | contains("@sha256:")' >/dev/null || { echo "Postfix image is not pinned by digest" >&2; exit 1; }
printf '%s\n' "$rendered_json" | jq -e '.services.ingress.image | contains("@sha256:")' >/dev/null || { echo "ingress image is not pinned by digest" >&2; exit 1; }
printf '%s\n' "$rendered_json" | jq -e '[.services[] | select(has("image")) | .image | contains("@sha256:")] | all' >/dev/null || { echo "every gateway image must be pinned by digest" >&2; exit 1; }
printf '%s\n' "$rendered_json" | jq -e '[.services[] | select((.network_mode // "") == "host" and has("ports"))] | length == 0' >/dev/null || { echo "host-networked gateway services must not also publish Compose ports" >&2; exit 1; }
printf '%s\n' "$rendered_json" | jq -e '[.services[].volumes[]?.source? // "" | select(contains("docker.sock"))] | length == 0' >/dev/null || { echo "Docker API socket must not be mounted" >&2; exit 1; }
printf '%s\n' "$rendered_json" | jq -e '.services["postfix-gateway"].network_mode == "host" and .services.ingress.network_mode == "host"' >/dev/null || { echo "gateway services must use host networking" >&2; exit 1; }

grep -Fq 'reject_unauth_destination' "$base_dir/postfix/gateway-init.sh" || { echo "open-relay rejection is missing" >&2; exit 1; }
grep -Fq 'relay_domains = $AGENT_MAIL_DOMAIN' "$base_dir/postfix/gateway-init.sh" || { echo "recipient-domain allow-list is missing" >&2; exit 1; }
grep -Fq 'direct Internet MX delivery is disabled' "$base_dir/postfix/gateway-init.sh" || { echo "direct MX fail-closed transport is missing" >&2; exit 1; }
grep -Fq 'enable_original_recipient = yes' "$base_dir/postfix/gateway-init.sh" || { echo "original envelope recipient preservation is missing" >&2; exit 1; }
grep -Fq 'smtpd_sasl_auth_enable = no' "$base_dir/postfix/gateway-init.sh" || { echo "gateway log/auth secret exposure controls are missing" >&2; exit 1; }
test -x "$base_dir/postfix/sanitize-log.sh" || { echo "log sanitizer is missing or not executable" >&2; exit 1; }
grep -Fq 'HOME_WIREGUARD_IP' "$base_dir/postfix/gateway-init.sh" || { echo "WireGuard-only home transport is missing" >&2; exit 1; }
grep -Fq 'respond @admin 404' "$base_dir/ingress/Caddyfile" || { echo "public gateway admin route is not denied" >&2; exit 1; }
grep -Fq 'HOME_WIREGUARD_IP' "$base_dir/ingress/Caddyfile" || { echo "ingress upstream is not WireGuard-scoped" >&2; exit 1; }

echo "SMTP gateway Compose and static policy validation passed; live network gates remain required."
