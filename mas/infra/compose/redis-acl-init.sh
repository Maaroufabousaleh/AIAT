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

# Per-deployment bootstrap password configured by the Redis service. It is
# disabled and removed from active sessions before application services start.
INIT_PASS="${REDIS_INIT_PASSWORD:?REDIS_INIT_PASSWORD must be configured}"
ROUTER_PASS="${ROUTER_PASSWORD:?ROUTER_PASSWORD must be configured}"
TOOLCACHE_PASS="${TOOLCACHE_PASSWORD:?TOOLCACHE_PASSWORD must be configured}"
ACL_FILE="${REDIS_ACL_FILE:-/acl-state/users.acl}"
ADMIN_AVAILABLE=0

case "$INIT_PASS" in
    *[!a-zA-Z0-9_-]*)
        echo "ERROR: REDIS_INIT_PASSWORD must use URL-safe characters." >&2
        exit 64
        ;;
esac
if [ "${#INIT_PASS}" -lt 32 ]; then
    echo "ERROR: REDIS_INIT_PASSWORD must be at least 32 characters." >&2
    exit 64
fi

saved_default_is_disabled() {
    [ -r "$ACL_FILE" ] || return 1
    [ "$(grep -c '^user default ' "$ACL_FILE" || true)" = "1" ] || return 1
    grep -Eq '^user default off( |$)' "$ACL_FILE"
}

verify_existing_default_disabled() {
    # App users cannot inspect ACLs. An explicitly provisioned read-only ACL
    # verifier is required on repeat runs; never infer security from PING.
    [ -n "${REDIS_ACL_VERIFY_USER:-}" ] || return 1
    [ -n "${REDIS_ACL_VERIFY_PASSWORD_FILE:-}" ] || return 1
    [ -r "$REDIS_ACL_VERIFY_PASSWORD_FILE" ] || return 1
    verify_password="$(cat "$REDIS_ACL_VERIFY_PASSWORD_FILE")"
    [ -n "$verify_password" ] || return 1
    flags="$(REDISCLI_AUTH="$verify_password" redis-cli --no-auth-warning --raw --user "$REDIS_ACL_VERIFY_USER" -h "$REDIS_HOST" -p "$REDIS_PORT" ACL GETUSER default 2>/dev/null | awk '/^flags$/{inflags=1; next} /^passwords$/{inflags=0} inflags {print}' || true)"
    printf '%s\n' "$flags" | grep -qx off || return 1
    if printf '%s\n' "$flags" | grep -qx on; then return 1; fi
    saved_default_is_disabled
}
i=0
while [ $i -lt $MAX_RETRIES ]; do
    if [ "$(redis-cli --no-auth-warning --user default -a "$INIT_PASS" -h "$REDIS_HOST" -p "$REDIS_PORT" ping 2>/dev/null || true)" = "PONG" ]; then
        ADMIN_AVAILABLE=1
        echo "Redis is ready!"
        break
    fi
    if [ "$(redis-cli --no-auth-warning --user router_user -a "$ROUTER_PASS" -h "$REDIS_HOST" -p "$REDIS_PORT" ping 2>/dev/null || true)" = "PONG" ] &&
        [ "$(redis-cli --no-auth-warning --user toolcache_user -a "$TOOLCACHE_PASS" -h "$REDIS_HOST" -p "$REDIS_PORT" ping 2>/dev/null || true)" = "PONG" ]; then
        # A persisted ACL may predate the router's atomic Lua operations.  Do
        # not silently report a healthy init while the deployed router would
        # receive NOPERM for EVAL.  The Redis service performs the narrow ACL
        # file migration before startup; this check verifies the loaded ACL.
        if [ "$(redis-cli --no-auth-warning --user router_user -a "$ROUTER_PASS" -h "$REDIS_HOST" -p "$REDIS_PORT" eval 'return 1' 0 2>/dev/null || true)" = "1" ]; then
            if verify_existing_default_disabled; then
                echo "Redis ACL users and disabled bootstrap state verified."
                exit 0
            fi
            echo "ERROR: cannot verify the effective and saved bootstrap ACL; refusing startup." >&2
            exit 1
        fi
        echo "ERROR: persisted router_user ACL does not permit EVAL; refusing to start the router." >&2
        exit 1
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
    +eval +evalsha \
    +ping +get +set +del +xgroup +xadd +xautoclaim +xclaim +xtrim +xack +xdel \
    +xreadgroup +xrange +xrevrange +xpending
echo "  - router_user configured"

# Configure toolcache_user with only cache CRUD commands.
expect_ok redis_admin \
    ACL SETUSER toolcache_user on ">${TOOLCACHE_PASS}" resetkeys -@all \
    "~tool_cache:*" "~shared:*" +ping +select +get +set +setex +del
echo "  - toolcache_user configured"

# Disable the bootstrap user and persist the disabled state using the same
# authenticated connection. Saving before disabling would re-enable the
# bootstrap administrator when Redis next loads users.acl.
output="$(printf 'ACL SETUSER default off\nCLIENT KILL USER default SKIPME YES\nACL SAVE\n' | redis-cli --no-auth-warning --user default -a "$INIT_PASS" -h "$REDIS_HOST" -p "$REDIS_PORT")"
first="$(printf '%s\n' "$output" | sed -n '1p')"
killed="$(printf '%s\n' "$output" | sed -n '2p')"
last="$(printf '%s\n' "$output" | sed -n '3p')"
case "$killed" in ''|*[!0-9]*) killed=invalid ;; esac
if [ "$first" != "OK" ] || [ "$killed" = invalid ] || [ "$last" != "OK" ] || [ "$(printf '%s\n' "$output" | wc -l | tr -d ' ')" != "3" ]; then
    echo "ERROR: could not disable and persist the bootstrap user." >&2
    exit 1
fi
if ! saved_default_is_disabled; then
    echo "ERROR: saved ACL does not show the disabled bootstrap user." >&2
    exit 1
fi
echo "  - default user disabled and ACL persisted"

echo ""
echo "ACL configuration complete. Restricted-user PING checks passed during startup."

exit 0
