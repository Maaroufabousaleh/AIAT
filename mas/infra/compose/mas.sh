#!/bin/bash
# MAS Container Management Script
# Usage: ./mas.sh [command]
# Commands: up, down, restart, logs, status, build

set -e

COMPOSE_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$COMPOSE_DIR"

# Load environment file if it exists
if [ -f .env ]; then
    export $(cat .env | grep -v '^#' | xargs)
fi

COMPOSE_FILES="-f docker-compose.yml -f docker-compose.dev.yml"

CMD="${1:-up}"

case "$CMD" in
    up)
        echo "Starting all MAS containers..."
        docker compose $COMPOSE_FILES up -d
        echo "Containers started. Use './mas.sh logs' to view logs."
        ;;
    down)
        echo "Stopping all MAS containers..."
        docker compose $COMPOSE_FILES down
        ;;
    restart)
        echo "Restarting all MAS containers..."
        docker compose $COMPOSE_FILES restart
        ;;
    logs)
        SERVICE="${2:-orchestrator-api}"
        echo "Following logs for $SERVICE (Ctrl+C to exit)..."
        docker compose $COMPOSE_FILES logs -f "$SERVICE"
        ;;
    status)
        echo "Container status:"
        docker compose $COMPOSE_FILES ps
        ;;
    build)
        echo "Building all images..."
        docker compose $COMPOSE_FILES build --parallel
        ;;
    rebuild)
        echo "Rebuilding all images (no cache)..."
        docker compose $COMPOSE_FILES build --no-cache --parallel
        ;;
    clean)
        echo "Stopping and removing all containers and volumes..."
        docker compose $COMPOSE_FILES down -v
        ;;
    ps)
        docker compose $COMPOSE_FILES ps
        ;;
    health)
        echo "Checking health of all services..."
        docker compose $COMPOSE_FILES ps --format "table {{.Name}}\t{{.Status}}\t{{.Health}}"
        ;;
    *)
        echo "Usage: $0 {up|down|restart|logs|status|build|rebuild|clean|ps|health}"
        echo ""
        echo "Commands:"
        echo "  up       - Start all containers (default)"
        echo "  down     - Stop all containers"
        echo "  restart  - Restart all containers"
        echo "  logs     - Follow logs (specify service, e.g., ./mas.sh logs orchestrator-api)"
        echo "  status   - Show container status"
        echo "  build    - Build all images"
        echo "  rebuild  - Rebuild all images (no cache)"
        echo "  clean    - Stop and remove all containers and volumes"
        echo "  ps       - Show running containers"
        echo "  health   - Show health status of all services"
        exit 1
        ;;
esac
