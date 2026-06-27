#!/bin/sh
# Compatibility shim for OmniRoute 3.8.38 + CLIProxyAPI 7.2.x.
# OmniRoute currently launches the binary with "-c" while CLIProxyAPI expects
# "-config". Remove this shim after the upstream launcher is corrected.
set -eu

self="$(readlink -f "$0")"
real_binary="${self}.real"

if [ "${1:-}" = "-c" ]; then
    shift
    set -- -config "$@"
fi

exec "$real_binary" "$@"
