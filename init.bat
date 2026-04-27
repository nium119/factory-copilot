@echo off
chcp 65001 >nul
echo ========================================
echo   Factory Copilot - Dev Environment
echo ========================================
echo.

REM 检查 Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found
    pause
    exit /b 1
)

REM 后端虚拟环境
cd /d "%~dp0backend"
if not exist "venv" (
    echo [CREATE] Creating venv...
    python -m venv venv
    if errorlevel 1 (
        echo [ERROR] Failed to create venv
        pause
        exit /b 1
    )
)

REM 安装后端依赖
echo [INSTALL] Backend dependencies...
call venv\Scripts\activate
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
if errorlevel 1 (
    echo [WARN] pip install failed, trying default index...
    pip install -r requirements.txt
)

REM 启动后端 (8001端口)
echo [START] Backend API :8001...
start "FactoryCopilot-API" cmd /k "cd /d %~dp0backend && venv\Scripts\activate && uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload"

REM 安装前端依赖
cd /d "%~dp0frontend"
if not exist "node_modules" (
    echo [INSTALL] Frontend dependencies...
    call npm install
)

REM 启动前端 (3000端口)
echo [START] Frontend :3000...
start "FactoryCopilot-Web" cmd /k "cd /d %~dp0frontend && npm run dev"

echo.
echo ========================================
echo   Services Starting:
echo     Backend API:  http://localhost:8001
echo     Frontend:     http://localhost:3000
echo     API Docs:     http://localhost:8001/docs
echo ========================================
