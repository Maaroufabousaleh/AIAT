#!/bin/sh
# Dedicated v0.16.7 -> v0.16.15 security-upgrade lifecycle.
set -eu

base_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
exec python3 "$base_dir/stalwart_security_upgrade.py" "$@"
