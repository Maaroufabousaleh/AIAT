#!/bin/sh
set -eu

base_dir="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
mode="${1:-}"
env_file="${2:-$base_dir/.env.smtp-gateway}"
case "$mode" in stage|activate) ;; *) echo "usage: $0 stage|activate ENV_FILE [--evidence FILE]" >&2; exit 2 ;; esac
shift 2 || true
evidence_file=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --evidence) evidence_file="${2:?--evidence needs a file}"; shift 2 ;;
    *) echo "unknown activation option: $1" >&2; exit 2 ;;
  esac
done
test -f "$env_file" || { echo "missing SMTP gateway environment file" >&2; exit 1; }

env_value() { awk -F= -v key="$1" '$1 == key {sub(/^[^=]*=/, ""); sub(/\r$/, ""); print; exit}' "$env_file"; }
topology="$(env_value DEPLOYMENT_TOPOLOGY)"
certified="$(env_value OUTBOUND_RELAY_CERTIFIED)"
test "$topology" = smtp_gateway_vps_home_stalwart_resend || { echo "gateway activation refused: wrong deployment topology" >&2; exit 1; }
"$base_dir/scripts/validate-gateway-compose.sh" "$env_file"

command -v docker >/dev/null 2>&1 || { echo "docker is required" >&2; exit 1; }
compose() { docker compose --env-file "$env_file" -f "$base_dir/docker-compose.yml" "$@"; }

if [ "$mode" = stage ]; then
  test "$certified" = false || { echo "gateway staging requires OUTBOUND_RELAY_CERTIFIED=false" >&2; exit 1; }
  compose up -d --no-build postfix-gateway log-sanitizer ingress
  "$base_dir/scripts/preflight-gateway.sh" "$env_file"
  echo "gateway staged with outbound relay certification still disabled; run live staging acceptance before activation."
  exit 0
fi

test "$certified" = true || { echo "production gateway activation refused: OUTBOUND_RELAY_CERTIFIED must remain false until live Resend certification" >&2; exit 1; }
test -n "$evidence_file" || evidence_file="$(env_value GATEWAY_EXTERNAL_PREFLIGHT_EVIDENCE)"
"$base_dir/scripts/preflight-gateway.sh" "$env_file" --allow-certified --evidence "$evidence_file"
compose up -d --no-build postfix-gateway log-sanitizer ingress
compose ps
echo "production SMTP gateway activated only after the complete live preflight and staging evidence gates."
