#!/bin/sh
set -eu

: "${BACKUP_FILE:?encrypted backup file is required}"
: "${BACKUP_DECRYPTION_IDENTITY_FILE:?age identity file is required}"
: "${RESTORE_CONFIRM:?set RESTORE_CONFIRM=AIAT_MAIL_EDGE_RESTORE after review}"
command -v age >/dev/null 2>&1 || { echo "age encryption tool is required" >&2; exit 1; }
test -r "$BACKUP_FILE" && test -r "$BACKUP_DECRYPTION_IDENTITY_FILE"
test "$RESTORE_CONFIRM" = "AIAT_MAIL_EDGE_RESTORE" || { echo "restore confirmation is invalid" >&2; exit 1; }
work_dir="/tmp/aiat-restore-$$"
trap 'rm -rf "$work_dir"' EXIT INT TERM
mkdir -p "$work_dir"
age -d -i "$BACKUP_DECRYPTION_IDENTITY_FILE" -o "$work_dir/backup.tar" "$BACKUP_FILE"
tar -C "$work_dir" -xf "$work_dir/backup.tar"
dump="$(find "$work_dir" -name 'identity-*.dump' -type f | head -n 1)"
test -n "$dump" && test -r "$dump" || { echo "identity database dump is absent" >&2; exit 1; }
pg_restore --clean --if-exists --no-owner --no-privileges --dbname="$PGDATABASE" "$dump"
if [ "${STALWART_RESTORE_OFFLINE:-false}" = "true" ]; then
  test -d "$work_dir/stalwart-config" && test -d "$work_dir/stalwart-data" || { echo "Stalwart archive payload is incomplete" >&2; exit 1; }
  # Remove hidden and non-hidden stale entries. A shell `*` leaves dotfiles
  # behind and can silently produce a mixed-version Stalwart restore.
  find /target/stalwart-config /target/stalwart-data -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
  cp -a "$work_dir/stalwart-config/." /target/stalwart-config/
  cp -a "$work_dir/stalwart-data/." /target/stalwart-data/
else
  echo "Identity database restored. Stalwart data was not restored; set STALWART_RESTORE_OFFLINE=true only after stopping Stalwart."
fi
