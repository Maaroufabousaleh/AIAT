#!/bin/sh
set -eu

umask 077
side="${1:-}"
out_dir="${2:-/secure/aiat/wireguard}"
force=0
if [ "${3:-}" = "--force" ]; then force=1; fi
command -v wg >/dev/null 2>&1 || { echo "wg is required" >&2; exit 1; }
case "$side" in gateway|home) ;; *) echo "usage: $0 gateway|home [output-dir] [--force]" >&2; exit 2 ;; esac
mkdir -p "$out_dir"

private="$out_dir/${side}.private"
public="$out_dir/${side}.public"
if [ "$force" -ne 1 ] && { test -e "$private" || test -e "$public"; }; then
  echo "refusing to overwrite existing $side key; use --force explicitly" >&2
  exit 1
fi
wg genkey >"$private"
wg pubkey <"$private" >"$public"
chmod 600 "$private" "$public"
printf '%s public key: ' "$side"; cat "$public"
echo "$side private key was written only to $out_dir"
