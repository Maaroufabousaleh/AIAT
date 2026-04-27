@echo off
REM MAS Codebase Guardian - Continuous Monitoring Script
REM Runs cleanup, documentation, and QA checks while agents are active

echo ============================================
echo MAS Codebase Guardian - Starting Monitor
echo ============================================
echo.

:LOOP
    REM Get current timestamp
    for /f "tokens=2 delims==" %%I in ('wmic os get localdatetime /value') do set datetime=%%I
    set TIMESTAMP=%datetime:~0,4%-%datetime:~4,2%-%datetime:~6,2% %datetime:~8,2%:%datetime:~10,2%:%datetime:~12,2%

    echo [%TIMESTAMP%] Checking for changes...

    REM Count modified files
    for /f %%i in ('git status --short ^| find /c /v ""') do set CHANGED=%%i

    if %CHANGED% GTR 0 (
        echo [%TIMESTAMP%] Found %CHANGED% changed files - agents are active
        echo.
        
        REM Check for new untracked files
        for /f %%i in ('git status --short ^| findstr "^??" ^| find /c /v ""') do set NEWFILES=%%i
        if %NEWFILES% GTR 0 (
            echo [%TIMESTAMP%] New untracked files: %NEWFILES%
        )

        REM Run cleanup checks
        echo [%TIMESTAMP%] Running cleanup checks...
        
        REM Check for TODO/FIXME comments in changed files
        for /f "tokens=*" %%f in ('git diff --name-only') do (
            for /f %%c in ('findstr /R /C:"#.*TODO" /C:"#.*FIXME" /C:"#.*HACK" /C:"#.*XXX" "%%f" 2^>nul ^| find /c /v ""') do (
                if %%c GTR 0 (
                    echo [%TIMESTAMP%]   %%f: %%c cleanup-target comments found
                )
            )
        )

        REM Check for phase references
        for /f "tokens=*" %%f in ('git diff --name-only') do (
            for /f %%c in ('findstr /R /C:"#.*Phase [0-9]" /C:"#.*phase [0-9]" "%%f" 2^>nul ^| find /c /v ""') do (
                if %%c GTR 0 (
                    echo [%TIMESTAMP%]   %%f: %%c phase-reference comments found
                )
            )
        )

        echo.
        echo [%TIMESTAMP%] Running Python linting...
        cd mas
        python -m ruff check --select=E,F,W --ignore=E501,E731 apps/ packages/ 2>nul
        if %ERRORLEVEL% NEQ 0 (
            echo [%TIMESTAMP%] Linting issues found - review needed
        ) else (
            echo [%TIMESTAMP%] Linting passed
        )
        cd ..
        echo.
    ) else (
        echo [%TIMESTAMP%] No changes detected - waiting...
    )

    echo [%TIMESTAMP%] Next check in 30 seconds...
    echo.
    timeout /t 30 /nobreak >nul
    goto LOOP
