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
ROUTER_PASS="${ROUTER_PASSWORD:-router_default_pass}"
TOOLCACHE_PASS="${TOOLCACHE_PASSWORD:-toolcache_default_pass}"
ADMIN_AVAILABLE=0
i=0
while [ $i -lt $MAX_RETRIES ]; do
    if [ "$(redis-cli --no-auth-warning --user default -a "$INIT_PASS" -h "$REDIS_HOST" -p "$REDIS_PORT" ping 2>/dev/null || true)" = "PONG" ]; then
        ADMIN_AVAILABLE=1
        echo "Redis is ready!"
        break
    fi
    if [ "$(redis-cli --no-auth-warning --user router_user -a "$ROUTER_PASS" -h "$REDIS_HOST" -p "$REDIS_PORT" ping 2>/dev/null || true)" = "PONG" ] &&
        [ "$(redis-cli --no-auth-warning --user toolcache_user -a "$TOOLCACHE_PASS" -h "$REDIS_HOST" -p "$REDIS_PORT" ping 2>/dev/null || true)" = "PONG" ]; then
        echo "Redis ACL users are already configured."
        exit 0
    fi
    i=$((i + 1))
    echo "Waiting... ($i/$MAX_RETRIES)"
    sleep $RETRY_INTERVAL
done

if [ $i -eq $MAX_RETRIES ]; then
    echo "ERROR: Redis did not become ready in time"
    exit 1
fi

if [ "$ADMIN_AVAILABLE" != "1" ]; then
    echo "ERROR: Redis admin user is unavailable and ACL users are not configured."
    exit 1
fi

echo "Configuring Redis ACL users..."

redis_admin() {
    redis-cli --no-auth-warning --user default -a "$INIT_PASS" -h "$REDIS_HOST" -p "$REDIS_PORT" "$@"
}

expect_ok() {
    output="$("$@")"
    if [ "$output" != "OK" ]; then
        echo "ERROR: Redis ACL command failed." >&2
        echo "$output" >&2
        exit 1
    fi
}

# Configure router_user with only the commands used by message-router.
expect_ok redis_admin \
    ACL SETUSER router_user on ">${ROUTER_PASS}" resetkeys -@all \
    "~stream:*" "~dedupe:*" "~heartbeat:*" \
    +ping +get +set +del +xgroup +xadd +xautoclaim +xclaim +xtrim +xack +xdel \
    +xreadgroup +xrange +xrevrange +xpending
echo "  - router_user configured"

# Configure toolcache_user with only cache CRUD commands.
expect_ok redis_admin \
    ACL SETUSER toolcache_user on ">${TOOLCACHE_PASS}" resetkeys -@all \
    "~tool_cache:*" "~shared:*" +ping +select +get +set +setex +del
echo "  - toolcache_user configured"

# Persist ACL to disk while the temporary admin identity is still available;
# the restricted router user must never receive ACL administration commands.
if redis_admin ACL SAVE > /dev/null 2>&1; then
    echo "  - ACL persisted to disk (ACL SAVE)"
else
    echo "  - ACL SAVE failed" >&2
    exit 1
fi

# Disable default user only after users.acl has been written.
expect_ok redis_admin ACL SETUSER default off
echo "  - default user disabled"

echo ""
echo "ACL configuration complete. Restricted-user PING checks passed during startup."

exit 0
