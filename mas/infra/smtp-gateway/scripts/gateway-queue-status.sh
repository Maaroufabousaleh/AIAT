#!/bin/sh
set -eu

queue_listing="$(postqueue -p 2>/dev/null || true)"
queue_depth="$(printf '%s\n' "$queue_listing" | awk '/^[A-F0-9]+[*!]?[[:space:]]/ {count++} END {print count + 0}')"
spool_bytes="$(du -sb /var/spool/postfix 2>/dev/null | awk '{print $1 + 0}')"
queue_limit="${GATEWAY_QUEUE_MAX_BYTES:-10737418240}"
status=healthy
if [ "${spool_bytes:-0}" -gt "$queue_limit" ]; then status=over_limit; fi
printf '{"status":"%s","queue_depth":%s,"spool_bytes":%s,"queue_limit_bytes":%s}\n' "$status" "$queue_depth" "${spool_bytes:-0}" "$queue_limit"
