#!/bin/sh
set -eu

# Pipe gateway logs through this filter before forwarding them to centralized
# operator logging. It removes mailbox addresses and IPs while preserving
# queue IDs and status words needed for delivery/queue diagnostics.
sed -E \
  -e 's/[[:alnum:]._%+-]+@[[:alnum:].-]+/[redacted-email]/g' \
  -e 's/([0-9]{1,3}\.){3}[0-9]{1,3}/[redacted-ip]/g'
