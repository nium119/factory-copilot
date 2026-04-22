@echo off
chcp 65001 >nul
echo ========================================
echo   Factory Copilot
echo ========================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found
    pause
    exit /b 1
)

echo [1/3] Check backend virtual environment...
cd backend
if not exist "venv" (
    echo [CREATE] Creating virtual environment...
    python -m venv venv
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment
        pause
        exit /b 1
    )
)

echo [2/3] Activate virtual environment and check dependencies...
call venv\Scripts\activate
pip list | findstr "fastapi" >nul
if errorlevel 1 (
    echo [INSTALL] Installing dependencies...
    pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
    if errorlevel 1 (
        echo [ERROR] Failed to install dependencies
        pause
        exit /b 1
    )
)

echo [3/3] Check frontend build...
if not exist "..\frontend\dist" (
    echo [WARNING] Frontend not built, please run npm run build first
    pause
    exit /b 1
)

echo.
echo ========================================
echo   Start Services
echo ========================================
echo.

echo [START] Backend service (port: 8000)...
start "Backend" cmd /k "venv\Scripts\activate && uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload"

timeout /t 3 /nobreak >nul

echo.
echo ========================================
echo   Services Started!
echo ========================================
echo.
echo   Backend API:  http://localhost:8000
echo   Frontend:     http://localhost:8000
echo   API Docs:     http://localhost:8000/docs
echo.
echo   Press any key to open browser...
pause >nul

start http://localhost:8000

echo.
echo System started, close this window will not stop services
echo To stop services, run stop.bat
pause
