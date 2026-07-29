#!/bin/sh
set -eu

base_dir="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
repo_root="$(CDPATH= cd -- "$base_dir/../.." && pwd)"
mail_edge_dir="$repo_root/mail-edge"
home_env="${1:-$base_dir/home/.env.gateway-home}"
mail_env="${2:-$mail_edge_dir/.env.mail-edge}"
test -f "$home_env" || { echo "missing gateway-home environment file" >&2; exit 1; }
test -f "$mail_env" || { echo "missing mail-edge environment file" >&2; exit 1; }
command -v docker >/dev/null 2>&1 || { echo "docker is required" >&2; exit 1; }
command -v jq >/dev/null 2>&1 || { echo "jq is required" >&2; exit 1; }
command -v ss >/dev/null 2>&1 || { echo "ss is required" >&2; exit 1; }
command -v wg >/dev/null 2>&1 || { echo "wg is required" >&2; exit 1; }

env_value() { awk -F= -v key="$1" '$1 == key {sub(/^[^=]*=/, ""); sub(/\r$/, ""); print; exit}' "$2"; }
home_wg_ip="$(env_value HOME_WIREGUARD_IP "$home_env")"
gateway_wg_ip="$(env_value GATEWAY_WIREGUARD_IP "$home_env")"
wg_interface="$(env_value WIREGUARD_INTERFACE "$home_env")"
test "$home_wg_ip" = "10.77.0.2" || { echo "home WireGuard address must be 10.77.0.2" >&2; exit 1; }
test "$gateway_wg_ip" = "10.77.0.1" || { echo "gateway WireGuard address must be 10.77.0.1" >&2; exit 1; }
test -n "$wg_interface" || { echo "WIREGUARD_INTERFACE is required" >&2; exit 1; }
handshake="$(wg show "$wg_interface" latest-handshakes 2>/dev/null || true)"
test -n "$handshake" || { echo "home WireGuard has no peer handshake" >&2; exit 1; }

rendered="$(cd "$mail_edge_dir" && HOME_WIREGUARD_IP="$home_wg_ip" GATEWAY_WIREGUARD_IP="$gateway_wg_ip" docker compose --env-file "$mail_env" -f docker-compose.yml -f "$base_dir/home/docker-compose.gateway-home.yml" config --format json)"
printf '%s\n' "$rendered" | jq -e --arg ip "$home_wg_ip" '[.services.stalwart.ports[] | select(.target == 25) | .host_ip == $ip] | any' >/dev/null || { echo "home Stalwart SMTP is not bound only to the WireGuard address" >&2; exit 1; }
printf '%s\n' "$rendered" | jq -e --arg ip "$home_wg_ip" '[.services.stalwart.ports[] | select(.target == 8080) | .host_ip == $ip] | any' >/dev/null || { echo "home Stalwart HTTP is not bound only to the WireGuard address" >&2; exit 1; }
printf '%s\n' "$rendered" | jq -e --arg ip "$home_wg_ip" '[.services["identity-service"].ports[] | select(.target == 8010) | .host_ip == $ip] | any' >/dev/null || { echo "home identity-service is not bound only to the WireGuard address" >&2; exit 1; }
printf '%s\n' "$rendered" | jq -e '[.services.ingress.profiles[]?] | index("gateway-home-disabled") != null' >/dev/null || { echo "home public ingress was not disabled" >&2; exit 1; }
printf '%s\n' "$rendered" | jq -e '[.services[]?.ports[]? | select((.target == 25) or (.target == 8010) or (.target == 8080)) | select((.host_ip // "") != "10.77.0.2")] | length == 0' >/dev/null || { echo "home management/SMTP ports are publicly bound" >&2; exit 1; }

listeners="$(ss -ltn 2>/dev/null || true)"
for port in 25 8080 8010; do
  printf '%s\n' "$listeners" | grep -Eq "10\.77\.0\.2:$port[[:space:]]" || { echo "home host is not listening on WireGuard TCP/$port" >&2; exit 1; }
  if printf '%s\n' "$listeners" | grep -Eq "(^|[[:space:]])(0\.0\.0\.0|\*):$port[[:space:]]"; then
    echo "home TCP/$port is publicly bound" >&2
    exit 1
  fi
done

echo "home gateway overlay bindings validate; router must still prove no public TCP/25 forward."
