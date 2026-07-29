#!/bin/sh
set -eu

base_dir="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
env_file="${1:-$base_dir/.env.smtp-gateway}"
test -f "$env_file" || { echo "missing SMTP gateway environment file" >&2; exit 1; }
env_value() { awk -F= -v key="$1" '$1 == key {sub(/^[^=]*=/, ""); sub(/\r$/, ""); print; exit}' "$env_file"; }
queue_path="$(env_value GATEWAY_QUEUE_PATH)"
max_bytes="$(env_value GATEWAY_QUEUE_MAX_BYTES)"
min_free_kb="$(env_value GATEWAY_QUEUE_MIN_FREE_KB)"
limit_mode="$(env_value GATEWAY_QUEUE_LIMIT_MODE)"
quota_evidence="$(env_value GATEWAY_QUEUE_QUOTA_EVIDENCE)"
case "$queue_path" in /*) ;; *) echo "queue disk validation refused: GATEWAY_QUEUE_PATH must be absolute" >&2; exit 1 ;; esac
test "$limit_mode" = filesystem_quota || { echo "queue disk validation refused: GATEWAY_QUEUE_LIMIT_MODE must be filesystem_quota" >&2; exit 1; }
test -f "$quota_evidence" || { echo "queue disk validation refused: missing filesystem quota evidence $quota_evidence" >&2; exit 1; }
grep -Eq '^GATEWAY_QUEUE_QUOTA=PASS$' "$quota_evidence" || { echo "queue disk validation refused: GATEWAY_QUEUE_QUOTA=PASS is missing" >&2; exit 1; }
test -d "$queue_path" || { echo "queue disk validation refused: missing queue path $queue_path" >&2; exit 1; }
command -v df >/dev/null 2>&1 || { echo "df is required" >&2; exit 1; }
command -v du >/dev/null 2>&1 || { echo "du is required" >&2; exit 1; }
available_kb="$(df -Pk "$queue_path" | awk 'NR == 2 {print $4}')"
used_bytes="$(du -sk "$queue_path" | awk '{print $1 * 1024}')"
test "$available_kb" -ge "$min_free_kb" || { echo "queue disk validation refused: minimum free space is exhausted" >&2; exit 1; }
test "$used_bytes" -le "$max_bytes" || { echo "queue disk validation refused: queue path exceeds GATEWAY_QUEUE_MAX_BYTES" >&2; exit 1; }

echo "queue path, free-space floor, and filesystem-quota mode validate; verify the host quota reports $max_bytes bytes before activation."
