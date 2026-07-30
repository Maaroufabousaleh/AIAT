#!/bin/sh
# The Python implementation uses argument arrays and JSON parsing so the
# RESEND_API_KEY value never becomes a process argument or rendered Compose
# value. This wrapper exists for consistent operator invocation and syntax QA.
set -eu

base_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
exec python3 "$base_dir/stalwart_secret_migration.py" "$@"
