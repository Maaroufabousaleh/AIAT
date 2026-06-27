#!/bin/sh
set -eu

psql -v ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  -v litellm_db=litellm \
  -v litellm_owner="$POSTGRES_USER" <<'EOSQL'
SELECT format('CREATE DATABASE %I OWNER %I', :'litellm_db', :'litellm_owner')
WHERE NOT EXISTS (
  SELECT 1 FROM pg_database WHERE datname = :'litellm_db'
)\gexec
EOSQL
