@echo off
REM AIAT MAS unified operational wrapper for Windows.
REM Usage: mas.bat [command] [service]

setlocal DisableDelayedExpansion

set "COMPOSE_DIR=%~dp0"
for %%I in ("%COMPOSE_DIR%..\..\..") do set "PROJECT_ROOT=%%~fI"
set "ENV_FILE=%PROJECT_ROOT%\.env"
if not exist "%ENV_FILE%" set "ENV_FILE=%COMPOSE_DIR%.env"

if exist "%ENV_FILE%" (
    REM eol=# ignores comments; tokens=1,* preserves '=' characters in values.
    for /f "usebackq eol=# tokens=1,* delims==" %%A in ("%ENV_FILE%") do set "%%A=%%B"
)
set "COMPOSE_DISABLE_ENV_FILE=1"
set COMPOSE=docker compose -f "%COMPOSE_DIR%docker-compose.yml" -f "%COMPOSE_DIR%docker-compose.dev.yml"

set "CMD=%~1"
if "%CMD%"=="" set "CMD=up"

if /i "%CMD%"=="up" goto :up
if /i "%CMD%"=="down" goto :down
if /i "%CMD%"=="restart" goto :restart
if /i "%CMD%"=="start" goto :start
if /i "%CMD%"=="stop" goto :stop
if /i "%CMD%"=="logs" goto :logs
if /i "%CMD%"=="tail" goto :tail
if /i "%CMD%"=="status" goto :status
if /i "%CMD%"=="ps" goto :status
if /i "%CMD%"=="health" goto :health
if /i "%CMD%"=="build" goto :build
if /i "%CMD%"=="rebuild" goto :rebuild
if /i "%CMD%"=="migrate" goto :migrate
if /i "%CMD%"=="migrate-status" goto :migrate_status
if /i "%CMD%"=="validate" goto :validate
if /i "%CMD%"=="validate-env" goto :validate
if /i "%CMD%"=="diag" goto :diagnostics
if /i "%CMD%"=="diagnostics" goto :diagnostics
if /i "%CMD%"=="clean" goto :clean
if /i "%CMD%"=="help" goto :help
if /i "%CMD%"=="--help" goto :help
if /i "%CMD%"=="-h" goto :help
echo Unknown command: %CMD%
goto :help_error

:up
echo Starting MAS containers...
%COMPOSE% up -d %2 %3 %4 %5 %6 %7 %8 %9
if errorlevel 1 goto :failed
echo Containers started. Dashboard: http://localhost:4000
goto :end

:down
echo Stopping MAS containers...
%COMPOSE% down %2 %3 %4 %5 %6 %7 %8 %9
if errorlevel 1 goto :failed
goto :end

:restart
if "%~2"=="" (
    %COMPOSE% restart
) else (
    %COMPOSE% restart "%~2"
)
if errorlevel 1 goto :failed
goto :end

:start
if "%~2"=="" (
    %COMPOSE% start
) else (
    %COMPOSE% start "%~2"
)
if errorlevel 1 goto :failed
goto :end

:stop
if "%~2"=="" (
    %COMPOSE% stop
) else (
    %COMPOSE% stop "%~2"
)
if errorlevel 1 goto :failed
goto :end

:logs
set "SERVICE=%~2"
if "%SERVICE%"=="" set "SERVICE=orchestrator-api"
%COMPOSE% logs -f --tail=200 "%SERVICE%"
goto :end

:tail
set "SERVICE=%~2"
set "LINES=%~3"
if "%SERVICE%"=="" set "SERVICE=orchestrator-api"
if "%LINES%"=="" set "LINES=100"
%COMPOSE% logs --tail=%LINES% "%SERVICE%"
if errorlevel 1 goto :failed
goto :end

:status
%COMPOSE% ps
if errorlevel 1 goto :failed
goto :end

:health
%COMPOSE% ps --format "table {{.Name}}	{{.Status}}	{{.Health}}"
if errorlevel 1 goto :failed
curl -fsS http://localhost:8000/health
if errorlevel 1 goto :failed
echo.
goto :end

:build
if "%~2"=="" (
    %COMPOSE% build --parallel
) else (
    %COMPOSE% build --parallel "%~2"
)
if errorlevel 1 goto :failed
goto :end

:rebuild
if "%~2"=="" (
    %COMPOSE% build --no-cache --parallel
) else (
    %COMPOSE% build --no-cache "%~2"
)
if errorlevel 1 goto :failed
goto :end

:migrate
%COMPOSE% run --rm orchestrator-api python -m alembic -c /app/alembic.ini upgrade heads
if errorlevel 1 goto :failed
goto :end

:migrate_status
%COMPOSE% run --rm orchestrator-api python -m alembic -c /app/alembic.ini current
if errorlevel 1 goto :failed
goto :end

:validate
set "MISSING=0"
for %%V in (POSTGRES_PASSWORD MINIO_ROOT_PASSWORD ROUTER_PASSWORD TOOLCACHE_PASSWORD ROUTER_SECRET TOOL_SECRET LLM_GATEWAY_URL MAS_API_KEY DASHBOARD_USERNAME DASHBOARD_PASSWORD_HASH JWT_SECRET) do (
    if not defined %%V (
        echo Missing required environment variable: %%V
        set "MISSING=1"
    )
)
if "%MISSING%"=="1" goto :failed
docker info >nul 2>&1
if errorlevel 1 goto :failed
%COMPOSE% config --quiet
if errorlevel 1 goto :failed
echo Environment, Docker, and Compose files are valid.
goto :end

:diagnostics
docker --version
docker compose version
%COMPOSE% ps
docker stats --no-stream --format "table {{.Name}}	{{.CPUPerc}}	{{.MemUsage}}"
docker system df
goto :end

:clean
echo This will remove ALL MAS containers and volumes. Data will be lost.
set "CONFIRM="
set /p "CONFIRM=Are you sure? [y/N] "
if /i not "%CONFIRM%"=="y" (
    echo Cleanup cancelled.
    goto :end
)
%COMPOSE% down -v
if errorlevel 1 goto :failed
echo Cleanup complete.
goto :end

:help
echo Usage: mas.bat [command] [service]
echo.
echo Lifecycle: up, down, restart, start, stop, logs, tail, status, health
echo Build: build, rebuild
echo Database: migrate, migrate-status
echo Operations: validate, diagnostics
echo Maintenance: clean ^(requires confirmation^)
goto :end

:help_error
endlocal
exit /b 2

:failed
echo Command failed: %CMD% 1>&2
endlocal
exit /b 1

:end
endlocal
exit /b 0
