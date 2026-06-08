#!/bin/sh
# ── Redis ACL init script ─────────────────────────────────────────────────
# This script waits for Redis to be ready, then configures ACL users.
# Run as an init container in Kubernetes, or as a one-shot service in Docker.
set -eu

# Redis connection settings
REDIS_HOST="${REDIS_HOST:-redis}"
REDIS_PORT="${REDIS_PORT:-6379}"
MAX_RETRIES=30
RETRY_INTERVAL=1

echo "Waiting for Redis at ${REDIS_HOST}:${REDIS_PORT}..."

# Password configured in redis.conf via `user default on >redis_init_temp_pass`
INIT_PASS="redis_init_temp_pass"
i=0
while [ $i -lt $MAX_RETRIES ]; do
    if redis-cli --user default -a "$INIT_PASS" -h "$REDIS_HOST" -p "$REDIS_PORT" ping > /dev/null 2>&1; then
        echo "Redis is ready!"
        break
    fi
    i=$((i + 1))
    echo "Waiting... ($i/$MAX_RETRIES)"
    sleep $RETRY_INTERVAL
done

if [ $i -eq $MAX_RETRIES ]; then
    echo "ERROR: Redis did not become ready in time"
    exit 1
fi

# Get passwords from environment (or use defaults for development)
ROUTER_PASS="${ROUTER_PASSWORD:-router_default_pass}"
TOOLCACHE_PASS="${TOOLCACHE_PASSWORD:-toolcache_default_pass}"

echo "Configuring Redis ACL users..."

# Configure router_user - use ACL categories (@stream) instead of individual commands
# which provides better compatibility with Redis 7 ACL changes
redis-cli --user default -a "$INIT_PASS" -h "$REDIS_HOST" -p "$REDIS_PORT" \
    ACL SETUSER router_user on ">${ROUTER_PASS}" "~stream:*" "~dedupe:*" "~heartbeat:*" \
    +@stream +@write +@read +@slow +ping
echo "  - router_user configured"

# Configure toolcache_user
redis-cli --user default -a "$INIT_PASS" -h "$REDIS_HOST" -p "$REDIS_PORT" \
    ACL SETUSER toolcache_user on ">${TOOLCACHE_PASS}" "~tool_cache:*" +@read +@write +@slow +ping +select
echo "  - toolcache_user configured"

# Disable default user
redis-cli --user default -a "$INIT_PASS" -h "$REDIS_HOST" -p "$REDIS_PORT" ACL SETUSER default off
echo "  - default user disabled"

# Persist ACL to disk when Redis is configured with an ACL file. The compose
# config defines users inline at startup, so ACL SAVE can be unavailable.
if redis-cli -u "redis://router_user:${ROUTER_PASS}@${REDIS_HOST}:${REDIS_PORT}" ACL SAVE > /dev/null 2>&1; then
    echo "  - ACL persisted to disk (ACL SAVE)"
else
    echo "  - ACL SAVE skipped; Redis is not configured with an ACL file"
fi

echo ""
echo "ACL configuration complete. Current users:"
redis-cli -u "redis://router_user:${ROUTER_PASS}@${REDIS_HOST}:${REDIS_PORT}" ACL LIST | head -20

exit 0
