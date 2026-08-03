#!/bin/sh
set -eu

base_dir="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
env_file="${1:-$base_dir/.env.mail-edge}"
test -f "$env_file" || { echo "missing mail-edge environment file" >&2; exit 1; }
command -v jq >/dev/null 2>&1 || { echo "jq is required" >&2; exit 1; }
export MAIL_EDGE_ENV_FILE="$env_file"
env_value() {
  key="$1"
  awk -F= -v key="$key" '$1 == key {sub(/^[^=]*=/, ""); sub(/\r$/, ""); print; exit}' "$env_file"
}
test "$(env_value DEPLOYMENT_TOPOLOGY)" = "self_hosted_stalwart_resend" || { echo "production mail-edge requires DEPLOYMENT_TOPOLOGY=self_hosted_stalwart_resend" >&2; exit 1; }
test "$(env_value IDENTITY_PROFILE)" = "production" || { echo "production mail-edge requires IDENTITY_PROFILE=production" >&2; exit 1; }
test "$(env_value AGENT_MAIL_DOMAIN)" = "agents.aiat.ca" || { echo "production mail-edge requires AGENT_MAIL_DOMAIN=agents.aiat.ca" >&2; exit 1; }
test "$(env_value MAIL_HOSTNAME)" = "mail.aiat.ca" || { echo "production mail-edge requires MAIL_HOSTNAME=mail.aiat.ca" >&2; exit 1; }
test "$(env_value IDENTITY_HOSTNAME)" = "identity.aiat.ca" || { echo "production mail-edge requires IDENTITY_HOSTNAME=identity.aiat.ca" >&2; exit 1; }
grep -q '^DIRECT_MX_OUTBOUND_ENABLED=false$' "$env_file" || { echo "direct MX outbound must be disabled" >&2; exit 1; }
docker compose --env-file "$env_file" -f "$base_dir/docker-compose.yml" config -q
rendered="$(docker compose --env-file "$env_file" -f "$base_dir/docker-compose.yml" config)"
rendered_json="$(docker compose --env-file "$env_file" -f "$base_dir/docker-compose.yml" config --format json)"
printf '%s\n' "$rendered" | grep -Eq 'target: 25' || { echo "inbound SMTP target mapping missing" >&2; exit 1; }
printf '%s\n' "$rendered" | grep -Eq 'target: 443' || { echo "HTTPS target mapping missing" >&2; exit 1; }
printf '%s\n' "$rendered" | grep -Eq 'published: "25"' || { echo "inbound SMTP published mapping missing" >&2; exit 1; }
printf '%s\n' "$rendered" | grep -Eq 'published: "443"' || { echo "HTTPS published mapping missing" >&2; exit 1; }
! printf '%s\n' "$rendered" | grep -Eq 'published: "8080"' || { echo "Stalwart bootstrap HTTP must not be publicly published" >&2; exit 1; }
printf '%s\n' "$rendered" | grep -Fq 'STALWART_PUBLIC_URL: http://stalwart:8080' || { echo "identity-service must use the private Stalwart listener" >&2; exit 1; }
jq -e '.services.stalwart.healthcheck.test | join(" ") | contains("/healthz/ready")' <<EOF >/dev/null || { echo "Stalwart readiness health check is missing" >&2; exit 1; }
$rendered_json
EOF
! grep -q 'tls_insecure_skip_verify' "$base_dir/Caddyfile" || { echo "Caddy must not disable upstream TLS verification" >&2; exit 1; }
test "$(jq -r '.mta_route["@type"]' "$base_dir/stalwart-relay-policy.json")" = "Relay" || { echo "relay policy must define a Relay route" >&2; exit 1; }
jq -e '.mta_route | .address == "smtp.resend.com" and .port == 465 and .implicitTls == true and .allowInvalidCerts == false and .authSecret == {"@type":"EnvironmentVariable","variableName":"RESEND_API_KEY"} and (has("name") | not)' "$base_dir/stalwart-relay-policy.json" >/dev/null || { echo "relay policy is not the approved environment-backed Resend route" >&2; exit 1; }
! grep -Eq '"@type"[[:space:]]*:[[:space:]]*"Mx"' "$base_dir/stalwart-relay-policy.json" || { echo "relay policy must not define a direct MX route" >&2; exit 1; }
jq -e '.services.stalwart.environment | has("RESEND_API_KEY") and (has("IDENTITY_DATABASE_PASSWORD") | not) and (has("IDENTITY_SERVICE_SECRET") | not)' <<EOF >/dev/null || { echo "Stalwart secret scope is invalid" >&2; exit 1; }
$rendered_json
EOF
jq -e '.services.ingress.environment | (has("RESEND_API_KEY") | not) and (has("STALWART_API_KEY") | not) and (has("IDENTITY_DATABASE_PASSWORD") | not)' <<EOF >/dev/null || { echo "ingress received an unrelated secret" >&2; exit 1; }
$rendered_json
EOF
jq -e '.services["identity-service"].environment | has("STALWART_API_KEY") and has("STALWART_JMAP_SERVICE_TOKEN") and has("RESEND_API_KEY") and (has("BACKUP_ENCRYPTION_RECIPIENT") | not)' <<EOF >/dev/null || { echo "identity-service secret scope is invalid" >&2; exit 1; }
$rendered_json
EOF
jq -e '[.services[]?.ports[]? | select((.target == 5432) or (.target == 8010) or (.target == 8080) or (.target == 18080)) | select((.host_ip // "") != "127.0.0.1")] | length == 0' <<EOF >/dev/null || { echo "Postgres, identity internal, and Stalwart admin ports must not be publicly published" >&2; exit 1; }
$rendered_json
EOF
echo "self-hosted mail-edge Compose validates; run the local and external self-hosting preflight before activation."
