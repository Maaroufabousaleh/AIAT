#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════════════
#  MAS Unified Operational Control Script
#  Usage: ./mas.sh [command] [options]
# ═══════════════════════════════════════════════════════════════════════════════

set -e

COMPOSE_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$COMPOSE_DIR/../../.." && pwd)"
cd "$COMPOSE_DIR"

# ── Environment loading ────────────────────────────────────────────────────────
load_env_file() {
    local file="$1"
    local line key value
    while IFS= read -r line || [ -n "$line" ]; do
        line="${line#"${line%%[![:space:]]*}"}"
        line="${line%"${line##*[![:space:]]}"}"
        [[ -z "$line" || "$line" == \#* || "$line" != *=* ]] && continue
        key="${line%%=*}"
        value="${line#*=}"
        key="${key%"${key##*[![:space:]]}"}"
        value="${value#"${value%%[![:space:]]*}"}"
        value="${value%"${value##*[![:space:]]}"}"
        [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
        if [[ "$value" == \"*\" && "$value" == *\" ]]; then
            value="${value:1:${#value}-2}"
        elif [[ "$value" == \'*\' && "$value" == *\' ]]; then
            value="${value:1:${#value}-2}"
        fi
        export "$key=$value"
    done < "$file"
}

ENV_FILE="$PROJECT_ROOT/.env"
if [ -f "$ENV_FILE" ]; then
    load_env_file "$ENV_FILE"
elif [ -f ".env" ]; then
    ENV_FILE="$COMPOSE_DIR/.env"
    load_env_file "$ENV_FILE"
fi

# This wrapper always loads the development overlay.  Keep its convenience
# image names separate from the production Compose contract: direct production
# invocations must provide every immutable *_IMAGE_REF value, while a personal
# local `mas.sh up --build` can build the checked-in services without requiring
# a registry digest first.  A caller-supplied value always wins, including a
# digest-bearing release image when the wrapper is used for a local smoke test.
set_development_image_defaults() {
    local var value
    local -a defaults=(
        "AIAT_TEAM_RUNNER_IMAGE_REF=mas/team-runner:dev"
        "AIAT_REDIS_ACL_INIT_IMAGE_REF=mas/redis-acl-init:dev"
        "AIAT_MESSAGE_ROUTER_IMAGE_REF=mas/message-router:dev"
        "AIAT_TOOL_SERVICE_IMAGE_REF=mas/tool-service:dev"
        "AIAT_OPENCODE_RUNTIME_IMAGE_REF=mas/opencode-runtime:dev"
        "AIAT_ORCHESTRATOR_IMAGE_REF=mas/orchestrator-api:dev"
        "AIAT_PM_GATEWAY_IMAGE_REF=mas/pm-gateway:dev"
        "AIAT_DASHBOARD_IMAGE_REF=mas/dashboard:dev"
    )
    for entry in "${defaults[@]}"; do
        var="${entry%%=*}"
        value="${entry#*=}"
        if [ -z "${!var:-}" ]; then
            export "$var=$value"
        fi
    done
    # These names are retained as development-only compatibility inputs for
    # existing .env files. Production must set the *_IMAGE_REF counterparts.
    if [ -z "${LITELLM_IMAGE_REF:-}" ]; then
        export LITELLM_IMAGE_REF="${LITELLM_IMAGE:-ghcr.io/berriai/litellm:main-stable}"
    fi
    if [ -z "${OMNIROUTE_IMAGE_REF:-}" ]; then
        export OMNIROUTE_IMAGE_REF="${OMNIROUTE_IMAGE:-diegosouzapw/omniroute:3.8.38}"
    fi
    # Development-only principal keys keep the local stack's identities
    # distinct. Production deployments must replace them with secret values
    # in the environment manifest; never use these defaults for release.
    if [ -z "${AIAT_CEO_API_KEY:-}" ]; then
        export AIAT_CEO_API_KEY="aiat-dev-ceo-key"
    fi
    if [ -z "${AIAT_WORKER_API_KEY:-}" ]; then
        export AIAT_WORKER_API_KEY="aiat-dev-worker-key"
    fi
    # Local development follows the default company manifest unless an
    # explicit IANA timezone was supplied by the operator.
    if [ -z "${AIAT_COMPANY_TIMEZONE:-}" ]; then
        export AIAT_COMPANY_TIMEZONE="UTC"
    fi
}

set_development_image_defaults

# The wrapper loads .env itself so values containing "$" (bcrypt hashes, API
# keys, etc.) are exported literally instead of parsed by Compose interpolation.
export COMPOSE_DISABLE_ENV_FILE=1

COMPOSE_FILES="-f docker-compose.yml -f docker-compose.dev.yml"
CMD="${1:-up}"

# ── Color helpers ──────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; NC='\033[0m'
info()    { echo -e "${CYAN}[MAS]${NC} $*"; }
success() { echo -e "${GREEN}[OK]${NC}  $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $*"; }
error()   { echo -e "${RED}[ERR]${NC} $*" >&2; }

# BuildKit on Docker Desktop may inspect extended attributes before applying
# .dockerignore. Pytest temp trees with restrictive modes can therefore break
# context transfer even though they are ignored. Remove only known disposable
# test artifacts after verifying that every target stays inside mas/.
prepare_build_context() {
    local context_root target resolved cleanup_failed=0 stage_root
    if [ -n "${MAS_BUILD_CONTEXT:-}" ]; then
        info "Using operator-provided Docker build context: $MAS_BUILD_CONTEXT"
        return 0
    fi
    context_root="$(cd "$PROJECT_ROOT/mas" && pwd -P)"
    for target in "$context_root/.tmp-pytest" "$context_root/.tmp-delete-me"; do
        [ -e "$target" ] || continue
        resolved="$(realpath -m -- "$target")"
        case "$resolved" in
            "$context_root"/.tmp-pytest|"$context_root"/.tmp-delete-me) ;;
            *) error "Refusing to clean unexpected build-context path: $resolved"; return 1 ;;
        esac
        warn "Removing disposable test artifact before Docker build: $resolved"
        chmod -R u+rwX -- "$resolved" 2>/dev/null || cleanup_failed=1
        rm -rf -- "$resolved" 2>/dev/null || cleanup_failed=1
        [ ! -e "$resolved" ] || cleanup_failed=1
    done
    if [ "$cleanup_failed" -eq 0 ]; then
        unset MAS_BUILD_CONTEXT
        return 0
    fi

    # DrvFS ACL corruption can make a directory unreadable even to WSL root.
    # Build from a clean Linux-filesystem snapshot instead of weakening ACLs or
    # allowing BuildKit to traverse the damaged path.
    stage_root="${AIAT_BUILD_CONTEXT_DIR:-/tmp/aiat-mas-build-context-${UID}}"
    case "$stage_root" in
        /tmp/aiat-mas-build-context-*) ;;
        *) error "Unsafe AIAT_BUILD_CONTEXT_DIR: $stage_root"; return 1 ;;
    esac
    warn "Using clean staged Docker context because repo temp ACLs are unreadable: $stage_root"
    rm -rf -- "$stage_root"
    mkdir -p -- "$stage_root"
    tar -C "$context_root" \
        --exclude='./.tmp-pytest' \
        --exclude='./.tmp-delete-me' \
        --exclude='./.venv*' \
        --exclude='./.uv-cache' \
        --exclude='./node_modules' \
        --exclude='*/node_modules' \
        --exclude='*/.next' \
        --exclude='*/test-results' \
        --exclude='*/playwright-report' \
        --exclude='*/__pycache__' \
        --exclude='*/.pytest_cache' \
        --exclude='*/.ruff_cache' \
        -cf - . | tar -C "$stage_root" -xf -
    export MAS_BUILD_CONTEXT="$stage_root"
}

# ── Validate environment before destructive commands ──────────────────────────
validate_env() {
    local missing=0
    local required_vars=(
        POSTGRES_PASSWORD MINIO_ROOT_PASSWORD
        ROUTER_PASSWORD TOOLCACHE_PASSWORD
        ROUTER_SECRET TOOL_SECRET
        LLM_GATEWAY_URL MAS_API_KEY AIAT_OPERATOR_API_KEY
        AIAT_CEO_API_KEY AIAT_WORKER_API_KEY
        DASHBOARD_USERNAME DASHBOARD_PASSWORD_HASH JWT_SECRET
    )
    for var in "${required_vars[@]}"; do
        if [ -z "${!var}" ]; then
            warn "Missing required env var: $var"
            missing=$((missing + 1))
        fi
    done
    if [ "$missing" -gt 0 ]; then
        error "$missing required environment variables are not set."
        error "Please configure $ENV_FILE before running."
        return 1
    fi
    success "All required environment variables are set."
}

case "$CMD" in
    # ── Lifecycle ──────────────────────────────────────────────────────────────
    up)
        info "Starting all MAS containers..."
        if [[ " $* " == *" --build "* ]]; then
            prepare_build_context
        fi
        docker compose $COMPOSE_FILES up -d "${@:2}"
        if [ "${AIAT_OMNIROUTE_BOOTSTRAP:-true}" != "false" ]; then
            info "Configuring OmniRoute providers and embedded services..."
            if command -v python3 >/dev/null 2>&1; then
                python3 "$COMPOSE_DIR/configure_omniroute.py" \
                    || warn "OmniRoute configuration did not complete; see the message above."
            else
                warn "python3 is unavailable; skipping OmniRoute configuration."
            fi
        fi
        success "Containers started. Dashboard: http://localhost:4000"
        success "LiteLLM analytics: http://localhost:4001/ui/"
        success "OmniRoute analytics: http://localhost:20128/dashboard/analytics"
        success "API: http://localhost:8000  Platform metrics: http://localhost:9090"
        ;;

    down)
        info "Stopping all MAS containers..."
        docker compose $COMPOSE_FILES down "${@:2}"
        success "All containers stopped."
        ;;

    restart)
        SERVICE="${2:-}"
        if [ -n "$SERVICE" ]; then
            info "Restarting service: $SERVICE"
            docker compose $COMPOSE_FILES restart "$SERVICE"
        else
            info "Restarting all MAS containers..."
            docker compose $COMPOSE_FILES restart
        fi
        success "Restart complete."
        ;;

    start)
        SERVICE="${2:-}"
        if [ -n "$SERVICE" ]; then
            info "Starting service: $SERVICE"
            docker compose $COMPOSE_FILES start "$SERVICE"
        else
            info "Starting all MAS containers..."
            docker compose $COMPOSE_FILES start
        fi
        ;;

    stop)
        SERVICE="${2:-}"
        if [ -n "$SERVICE" ]; then
            info "Stopping service: $SERVICE"
            docker compose $COMPOSE_FILES stop "$SERVICE"
        else
            info "Stopping all MAS containers..."
            docker compose $COMPOSE_FILES stop
        fi
        ;;

    # ── Build ──────────────────────────────────────────────────────────────────
    build)
        prepare_build_context
        SERVICE="${2:-}"
        if [ -n "$SERVICE" ]; then
            info "Building image for: $SERVICE"
            docker compose $COMPOSE_FILES build --parallel "$SERVICE"
        else
            info "Building all images..."
            docker compose $COMPOSE_FILES build --parallel
        fi
        success "Build complete."
        ;;

    rebuild)
        prepare_build_context
        SERVICE="${2:-}"
        if [ -n "$SERVICE" ]; then
            info "Rebuilding (no cache): $SERVICE"
            docker compose $COMPOSE_FILES build --no-cache "$SERVICE"
        else
            info "Rebuilding all images (no cache)..."
            docker compose $COMPOSE_FILES build --no-cache --parallel
        fi
        success "Rebuild complete."
        ;;

    # ── Database ───────────────────────────────────────────────────────────────
    migrate)
        info "Running Alembic migrations..."
        docker compose $COMPOSE_FILES run --rm orchestrator-api \
            python -m alembic -c /app/alembic.ini upgrade heads
        success "Migrations applied."
        ;;

    migrate-status)
        info "Checking migration status..."
        docker compose $COMPOSE_FILES run --rm orchestrator-api \
            python -m alembic -c /app/alembic.ini current
        ;;

    migrate-history)
        info "Migration history:"
        docker compose $COMPOSE_FILES run --rm orchestrator-api \
            python -m alembic -c /app/alembic.ini history --verbose
        ;;

    migrate-rollback)
        STEPS="${2:-1}"
        warn "Rolling back $STEPS migration step(s)..."
        docker compose $COMPOSE_FILES run --rm orchestrator-api \
            python -m alembic -c /app/alembic.ini downgrade "-$STEPS"
        success "Rollback complete."
        ;;

    init-db)
        info "Initializing database (migrate + seed)..."
        docker compose $COMPOSE_FILES run --rm orchestrator-api \
            python -m alembic -c /app/alembic.ini upgrade heads
        docker compose $COMPOSE_FILES run --rm orchestrator-api \
            python -c "
import asyncio
from mas_core.worker_registry.seeder import seed_worker_registry
asyncio.run(seed_worker_registry())
print('Worker registry seeded.')
" 2>/dev/null || warn "Seeder not available, skipping."
        success "Database initialized."
        ;;

    # ── Logs & Diagnostics ─────────────────────────────────────────────────────
    logs)
        SERVICE="${2:-orchestrator-api}"
        LINES="${3:-200}"
        info "Following logs for $SERVICE (Ctrl+C to exit)..."
        docker compose $COMPOSE_FILES logs -f --tail="$LINES" "$SERVICE"
        ;;

    logs-all)
        info "Following logs for all services (Ctrl+C to exit)..."
        docker compose $COMPOSE_FILES logs -f --tail=50
        ;;

    tail)
        SERVICE="${2:-orchestrator-api}"
        LINES="${3:-100}"
        docker compose $COMPOSE_FILES logs --tail="$LINES" "$SERVICE"
        ;;

    status)
        info "Container status:"
        docker compose $COMPOSE_FILES ps
        ;;

    ps)
        docker compose $COMPOSE_FILES ps
        ;;

    health)
        info "Health check for all services:"
        docker compose $COMPOSE_FILES ps --format "table {{.Name}}\t{{.Status}}\t{{.Health}}"
        echo ""
        info "API health endpoint:"
        curl -sf "http://localhost:8000/health" 2>/dev/null && echo "" || warn "orchestrator-api not reachable at :8000"
        info "Message router health:"
        docker compose $COMPOSE_FILES exec -T message-router \
            python -c "import httpx; print(httpx.get('http://localhost:8001/health').text)" 2>/dev/null \
            || warn "message-router health check failed"
        info "Tool service health:"
        docker compose $COMPOSE_FILES exec -T tool-service \
            python -c "import httpx; print(httpx.get('http://localhost:8002/health').text)" 2>/dev/null \
            || warn "tool-service health check failed"
        ;;

    diag|diagnostics)
        info "=== MAS Diagnostics ==="
        echo ""
        info "Docker version:"
        docker --version
        docker compose version
        echo ""
        info "Container states:"
        docker compose $COMPOSE_FILES ps 2>/dev/null || warn "Compose not running"
        echo ""
        info "Resource usage (top 5 by CPU):"
        docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}" 2>/dev/null | head -7 || warn "No containers running"
        echo ""
        info "Recent errors from orchestrator-api:"
        docker compose $COMPOSE_FILES logs --tail=20 orchestrator-api 2>/dev/null | grep -i "error\|exception\|critical" | tail -10 || true
        echo ""
        info "Disk usage (volumes):"
        docker system df 2>/dev/null || true
        ;;

    stats)
        info "Live container resource usage (Ctrl+C to exit):"
        docker stats --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}\t{{.BlockIO}}"
        ;;

    # ── Shell Access ───────────────────────────────────────────────────────────
    exec|shell)
        SERVICE="${2:-orchestrator-api}"
        SHELL_CMD="${3:-bash}"
        info "Opening shell in $SERVICE..."
        docker compose $COMPOSE_FILES exec "$SERVICE" "$SHELL_CMD"
        ;;

    run)
        SERVICE="${2:-orchestrator-api}"
        shift 2
        info "Running command in $SERVICE: $*"
        docker compose $COMPOSE_FILES exec "$SERVICE" "$@"
        ;;

    # ── Cleanup ────────────────────────────────────────────────────────────────
    clean)
        warn "This will remove ALL containers and volumes. Data will be lost."
        read -r -p "Are you sure? [y/N] " confirm
        if [[ "$confirm" =~ ^[Yy]$ ]]; then
            docker compose $COMPOSE_FILES down -v
            success "Cleanup complete."
        else
            info "Cleanup cancelled."
        fi
        ;;

    clean-volumes)
        warn "Stopping containers and removing volumes (data will be lost)..."
        docker compose $COMPOSE_FILES down -v --remove-orphans
        success "Containers stopped and volumes removed."
        ;;

    prune)
        info "Pruning unused Docker resources..."
        docker system prune -f
        success "Prune complete."
        ;;

    # ── Validation ─────────────────────────────────────────────────────────────
    validate|validate-env)
        info "Validating environment configuration..."
        validate_env
        info "Checking Docker availability..."
        docker info >/dev/null 2>&1 && success "Docker daemon is running." || error "Docker daemon is not running."
        info "Checking compose files..."
        docker compose $COMPOSE_FILES config --quiet 2>/dev/null && success "Compose files are valid." || error "Compose file validation failed."
        ;;

    # ── Systemd integration ────────────────────────────────────────────────────
    systemd-install)
        UNIT_DIR="/etc/systemd/system"
        UNIT_SRC="$PROJECT_ROOT/mas/infra/systemd"
        if [ -d "$UNIT_SRC" ]; then
            info "Installing systemd unit files..."
            sudo cp "$UNIT_SRC"/*.service "$UNIT_DIR/" 2>/dev/null || warn "No .service files found in $UNIT_SRC"
            sudo systemctl daemon-reload
            success "Systemd units installed. Enable with: sudo systemctl enable mas"
        else
            warn "No systemd unit directory found at $UNIT_SRC"
        fi
        ;;

    systemd-status)
        systemctl status mas 2>/dev/null || warn "MAS systemd service not installed"
        ;;

    # ── Help ───────────────────────────────────────────────────────────────────
    help|--help|-h|*)
        echo -e "${CYAN}MAS Unified Operational Control${NC}"
        echo ""
        echo "Usage: $0 <command> [options]"
        echo ""
        echo -e "${YELLOW}Lifecycle:${NC}"
        echo "  up [service]          Start all containers (or specific service)"
        echo "  down [--volumes]      Stop all containers"
        echo "  restart [service]     Restart all or specific service"
        echo "  start [service]       Start stopped containers"
        echo "  stop [service]        Stop running containers"
        echo ""
        echo -e "${YELLOW}Build:${NC}"
        echo "  build [service]       Build images (parallel)"
        echo "  rebuild [service]     Rebuild images (no cache)"
        echo ""
        echo -e "${YELLOW}Database:${NC}"
        echo "  migrate               Apply all pending Alembic migrations"
        echo "  migrate-status        Show current migration revision"
        echo "  migrate-history       Show full migration history"
        echo "  migrate-rollback [n]  Roll back n migration steps (default: 1)"
        echo "  init-db               Run migrate + seed worker registry"
        echo ""
        echo -e "${YELLOW}Logs & Diagnostics:${NC}"
        echo "  logs [service] [n]    Follow logs (default: orchestrator-api, 200 lines)"
        echo "  logs-all              Follow logs for all services"
        echo "  tail [service] [n]    Print last n lines (default: 100)"
        echo "  health                Check health of all services"
        echo "  diag                  Full diagnostics report"
        echo "  stats                 Live resource usage"
        echo "  status / ps           Show container status"
        echo ""
        echo -e "${YELLOW}Shell Access:${NC}"
        echo "  exec [service] [cmd]  Open interactive shell (default: bash)"
        echo "  run [service] <cmd>   Run a command in a running container"
        echo ""
        echo -e "${YELLOW}Maintenance:${NC}"
        echo "  clean                 Remove all containers + volumes (destructive)"
        echo "  clean-volumes         Stop containers and remove all volumes (destructive)"
        echo "  prune                 Prune unused Docker resources"
        echo "  validate              Validate env and compose files"
        echo ""
        echo -e "${YELLOW}Systemd:${NC}"
        echo "  systemd-install       Install systemd unit files"
        echo "  systemd-status        Check MAS systemd service status"
        echo ""
        echo -e "${YELLOW}Examples:${NC}"
        echo "  $0 up                 # Start everything"
        echo "  $0 logs ceo-team 500  # Last 500 lines from CEO team"
        echo "  $0 exec postgres psql -U mas # PostgreSQL shell"
        echo "  $0 migrate            # Apply DB migrations"
        echo "  $0 diag               # Full diagnostics report"
        if [ "$CMD" != "help" ] && [ "$CMD" != "--help" ] && [ "$CMD" != "-h" ]; then
            echo ""
            error "Unknown command: $CMD"
            exit 1
        fi
        ;;
esac
