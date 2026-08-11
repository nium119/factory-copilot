@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
echo ========================================
echo   Factory Copilot - Stop
echo ========================================
echo.

call :stop_port 9004 "Backend"
call :stop_port 5004 "Frontend"

echo.
echo   Done.
pause
exit /b

:stop_port
set PORT=%~1
set LABEL=%~2
echo [STOP] %LABEL% ^(port %PORT%^)...
set FOUND=0
rem Match port:non-digit boundary so :5001 won't catch :50010
for /f "tokens=5" %%a in ('netstat -ano ^| findstr /R ":%PORT%[^0-9].*LISTENING" 2^>nul') do (
    set FOUND=1
    set PID=%%a
    if "!PID!"=="4" (
        echo   Port held by HTTP.sys ^(PID 4^), trying netsh...
        netsh http delete urlacl url=http://+:%PORT%/ >nul 2>&1
        if !errorlevel! NEQ 0 (
            echo   [FAILED] Need Administrator privileges to release HTTP.sys reservation.
            echo   Please re-run stop.bat as Administrator.
        ) else (
            echo   Released HTTP.sys reservation.
        )
    ) else (
        taskkill /F /PID !PID! >nul 2>&1
        if !errorlevel! EQU 0 (
            echo   Stopped PID !PID!.
        ) else (
            echo   [FAILED] Cannot kill PID !PID!. Try running as Administrator.
        )
    )
)
if !FOUND! EQU 0 (
    echo   No process on this port.
    exit /b
)
exit /b
