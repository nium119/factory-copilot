@echo off
chcp 65001 >nul
echo ========================================
echo   Factory Copilot - Stop
echo ========================================
echo.

echo [STOP] Stopping services...

tasklist | findstr "python.exe" >nul
if not errorlevel 1 (
    echo [STOP] Backend service...
    taskkill /F /IM python.exe >nul 2>&1
)

echo.
echo ========================================
echo   Services Stopped
echo ========================================
echo.
pause
