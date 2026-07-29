#!/bin/sh
set -eu

base_dir="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
env_file="${1:-$base_dir/profiles/oci-e2.1-micro-host.env}"
output=""
shift || true
while [ "$#" -gt 0 ]; do
  case "$1" in
    --evidence) output="${2:?--evidence needs a file}"; shift 2 ;;
    *) echo "unknown adoption option: $1" >&2; exit 2 ;;
  esac
done
test -f "$env_file" || { echo "missing host gateway profile" >&2; exit 1; }
env_value() { awk -F= -v key="$1" '$1 == key {sub(/^[^=]*=/, ""); sub(/\r$/, ""); print; exit}' "$env_file"; }
transport_file="$(env_value HOST_POSTFIX_TRANSPORT_FILE)"
transport_db="${transport_file}.db"
forward_unit="$(env_value HOME_FORWARD_UNIT)"

echo "READ_ONLY_HOST_POSTFIX_ADOPTION=START"
echo "Existing WireGuard private keys are not read or replaced."
echo "Existing Postfix queue is not stopped, deleted, rebuilt, or recreated."
"$base_dir/scripts/validate-host-postfix.sh" "$env_file"
if [ -f "$transport_db" ]; then
  echo "TRANSPORT_DB_PRESENT=PASS"
else
  echo "TRANSPORT_DB_PRESENT=REVIEW_REQUIRED"
fi
if [ "$forward_unit" != "<existing-socat-systemd-unit>" ]; then
  systemctl is-active --quiet "$forward_unit" || { echo "adoption refused: configured socat unit is not active" >&2; exit 1; }
else
  systemctl list-units --type=service --all --no-legend | grep -Eiq 'socat' || { echo "adoption refused: no socat systemd unit was detected" >&2; exit 1; }
fi

# The collector writes only a sanitized fact report. It never calls postmap,
# postconf -e, systemctl restart, wg-quick down/up, or Docker Compose.
if [ -n "$output" ]; then
  "$base_dir/scripts/collect-host-evidence.sh" "$env_file" --output "$output"
else
  "$base_dir/scripts/collect-host-evidence.sh" "$env_file"
fi
echo "READ_ONLY_HOST_POSTFIX_ADOPTION=PASS"
echo "No live service or provider configuration was modified."
