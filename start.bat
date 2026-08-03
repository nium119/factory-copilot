@echo off
echo ========================================
echo   Factory Copilot - Dev
echo ========================================
echo.

cd /d "%~dp0"

if not exist "backend" (echo [ERROR] backend missing && pause && exit /b 1)
if not exist "frontend" (echo [ERROR] frontend missing && pause && exit /b 1)

echo [1/2] Backend :9004...

if not exist "backend\venv" if not exist "backend\.venv" (
    echo   Creating virtual environment...
    cd /d "%~dp0backend"
    python -m venv venv
    if errorlevel 1 (
        echo   [ERROR] Failed to create venv
        pause
        exit /b 1
    )
)

if exist "backend\venv\Scripts\activate.bat" (
    start "FactoryCopilot-BE" cmd /c "cd /d "%~dp0backend" && call venv\Scripts\activate.bat && pip install -r requirements.txt -q -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn && python -m uvicorn app.main:app --host 0.0.0.0 --port 9004 --reload"
) else if exist "backend\.venv\Scripts\activate.bat" (
    start "FactoryCopilot-BE" cmd /c "cd /d "%~dp0backend" && call .venv\Scripts\activate.bat && pip install -r requirements.txt -q -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn && python -m uvicorn app.main:app --host 0.0.0.0 --port 9004 --reload"
) else (
    start "FactoryCopilot-BE" cmd /c "cd /d "%~dp0backend" && python -m uvicorn app.main:app --host 0.0.0.0 --port 9004 --reload"
)

echo [2/2] Frontend :5004...
if not exist "frontend\node_modules" (
    echo   Installing frontend dependencies...
    cd /d "%~dp0frontend"
    call npm install
)
start "FactoryCopilot-FE" cmd /c "cd /d "%~dp0frontend" && npm run dev"

echo.
echo   Backend:  http://localhost:9004/docs
echo   Frontend: http://localhost:5004
echo.
start http://localhost:5004
pause
