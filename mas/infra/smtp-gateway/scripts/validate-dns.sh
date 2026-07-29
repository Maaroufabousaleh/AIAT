#!/bin/sh
set -eu

base_dir="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
env_file="${1:-$base_dir/.env.smtp-gateway}"
test -f "$env_file" || { echo "missing SMTP gateway environment file" >&2; exit 1; }
command -v dig >/dev/null 2>&1 || { echo "dig is required for gateway DNS validation" >&2; exit 1; }

env_value() { awk -F= -v key="$1" '$1 == key {sub(/^[^=]*=/, ""); sub(/\r$/, ""); print; exit}' "$env_file"; }
public_ip="$(env_value SMTP_GATEWAY_PUBLIC_IP)"
mail_host="$(env_value MAIL_HOSTNAME)"
identity_host="$(env_value IDENTITY_HOSTNAME)"
agent_domain="$(env_value AGENT_MAIL_DOMAIN)"
test "$mail_host" = mail.aiat.ca || { echo "DNS validation refused: MAIL_HOSTNAME must be mail.aiat.ca" >&2; exit 1; }
test "$identity_host" = identity.aiat.ca || { echo "DNS validation refused: IDENTITY_HOSTNAME must be identity.aiat.ca" >&2; exit 1; }
test "$agent_domain" = agents.aiat.ca || { echo "DNS validation refused: AGENT_MAIL_DOMAIN must be agents.aiat.ca" >&2; exit 1; }
case "$public_ip" in *[!0-9.]*|"") echo "DNS validation refused: SMTP_GATEWAY_PUBLIC_IP must be a real IPv4" >&2; exit 1 ;; esac

mail_records="$(dig +short A "$mail_host")"
identity_records="$(dig +short A "$identity_host")"
test "$(printf '%s\n' "$mail_records" | sed '/^$/d' | wc -l | tr -d ' ')" = 1 && printf '%s\n' "$mail_records" | grep -Fx "$public_ip" >/dev/null || { echo "mail A record must be exactly the gateway IPv4 and DNS-only" >&2; exit 1; }
test "$(printf '%s\n' "$identity_records" | sed '/^$/d' | wc -l | tr -d ' ')" = 1 && printf '%s\n' "$identity_records" | grep -Fx "$public_ip" >/dev/null || { echo "identity A record must be exactly the gateway IPv4 and DNS-only" >&2; exit 1; }
mx_records="$(dig +short MX "$agent_domain")"
printf '%s\n' "$mx_records" | awk '$1 == "10" && $2 == "mail.aiat.ca." {found++} END {exit(found == 1 ? 0 : 1)}' || { echo "agents MX must be exactly priority 10 mail.aiat.ca." >&2; exit 1; }

echo "gateway DNS records validate: mail/identity A to the gateway and agents MX to mail.aiat.ca."
