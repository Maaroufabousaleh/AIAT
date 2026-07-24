#!/bin/sh
set -eu

command -v age >/dev/null 2>&1 || { echo "age encryption tool is required" >&2; exit 1; }
: "${BACKUP_ENCRYPTION_RECIPIENT:?backup encryption recipient is required}"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
tmp="/tmp/aiat-mail-edge-${stamp}.tar"
work_dir="/tmp/aiat-mail-edge-backup-$$"
trap 'rm -rf "$work_dir" "$tmp"' EXIT INT TERM
mkdir -p "$work_dir"
test -d /source/stalwart-config && test -d /source/stalwart-data || {
  echo "Stalwart source directories are absent" >&2
  exit 1
}
pg_dump --format=custom --file="$work_dir/identity-${stamp}.dump"
# PostgreSQL is captured only with pg_dump; copying its live data directory
# would create a crash-inconsistent second database backup.
cp -a /source/stalwart-config /source/stalwart-data "$work_dir/"
tar -C "$work_dir" -cf "$tmp" stalwart-config stalwart-data "identity-${stamp}.dump"
age -r "$BACKUP_ENCRYPTION_RECIPIENT" -o "/backups/aiat-mail-edge-${stamp}.tar.age" "$tmp"
