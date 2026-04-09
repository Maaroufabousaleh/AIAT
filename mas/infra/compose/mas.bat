@echo off
REM MAS Container Management Script (Windows)
REM Usage: mas.bat [command]

setlocal enabledelayedexpansion

set "COMPOSE_DIR=%~dp0"
cd /d "%COMPOSE_DIR%"

set "CMD=%~1"
if "%CMD%"=="" set "CMD=up"

if exist ".env" (
    for /f "usebackq tokens=*" %%a in (.env) do set "%%a"
)

goto :%CMD%

:up
echo Starting all MAS containers...
docker compose up -d
echo Containers started. Use "mas.bat logs" to view logs.
goto :end

:down
echo Stopping all MAS containers...
docker compose down
goto :end

:restart
echo Restarting all MAS containers...
docker compose restart
goto :end

:logs
set "SERVICE=%~2"
if "%SERVICE%"=="" set "SERVICE=orchestrator-api"
echo Following logs for %SERVICE% (Ctrl+C to exit)...
docker compose logs -f %SERVICE%
goto :end

:status
echo Container status:
docker compose ps
goto :end

:build
echo Building all images...
docker compose build --parallel
goto :end

:rebuild
echo Rebuilding all images (no cache)...
docker compose build --no-cache --parallel
goto :end

:clean
echo Stopping and removing all containers and volumes...
docker compose down -v
goto :end

:ps
docker compose ps
goto :end

:health
echo Checking health of all services...
docker compose ps --format "table {{.Name}}	{{.Status}}	{{.Health}}"
goto :end

:help
echo Usage: mas.bat [command]
echo.
echo Commands:
echo   up       - Start all containers (default)
echo   down     - Stop all containers
echo   restart  - Restart all containers
echo   logs     - Follow logs (specify service, e.g., mas.bat logs orchestrator-api)
echo   status   - Show container status
echo   build    - Build all images
echo   rebuild  - Rebuild all images (no cache)
echo   clean    - Stop and remove all containers and volumes
echo   ps       - Show running containers
echo   health   - Show health status of all services
goto :end

:end
endlocal
