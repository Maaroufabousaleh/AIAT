#!/bin/sh
# Sole operator entrypoint for the governed local route-activation finish.
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ "$(id -u)" -ne 0 ]; then
  echo "FINAL_STATUS=BLOCKED" >&2
  echo "BLOCK_REASON=run the governed route activation as root" >&2
  exit 1
fi

exec python3 "$script_dir/finish_resend_route_activation.py" "$@"
