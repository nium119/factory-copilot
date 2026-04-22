# 生产环境部署指南 (Windows)

## 系统要求

- Windows 10/11 或 Windows Server 2019+
- Python 3.9+
- Node.js 16+
- Git

## 一、环境准备

### 1. 安装Python

```powershell
# 下载Python 3.9+ (推荐3.11)
# https://www.python.org/downloads/

# 验证安装
python --version
pip --version
```

### 2. 安装Node.js

```powershell
# 下载Node.js 16+ (推荐18 LTS)
# https://nodejs.org/

# 验证安装
node --version
npm --version
```

### 3. 安装Git

```powershell
# 下载Git
# https://git-scm.com/download/win

# 验证安装
git --version
```

## 二、后端部署

### 1. 创建虚拟环境

```powershell
cd backend

# 创建虚拟环境
python -m venv venv

# 激活虚拟环境
.\venv\Scripts\activate

# 升级pip
python -m pip install --upgrade pip
```

### 2. 安装依赖

```powershell
# 安装生产依赖 (使用清华源加速)
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### 3. 配置环境变量

创建 `.env` 文件:

```env
# 应用配置
APP_NAME=Factory Copilot
APP_ENV=production
DEBUG=false
API_PREFIX=/api

# 数据库配置 (SQLite)
DATABASE_URL=sqlite+aiosqlite:///./data/agent.db

# 记忆配置 (SQLite 向量存储)
MEMORY_ENABLED=true
MEMORY_AUTO_INJECT=true
MEMORY_TOP_K=5

# 模型配置
AGENT_MODEL=qwen3.6-plus

# API密钥
DASHSCOPE_API_KEY=your_dashscope_api_key

# 其他配置
LOG_LEVEL=INFO
MAX_HISTORY_LENGTH=50
```

### 4. 初始化数据库

```powershell
# 创建数据目录
mkdir data

# 运行数据库初始化
python scripts/init_db.py
```

### 5. 启动后端服务

#### 方式1: 使用uvicorn (开发/测试)

```powershell
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

#### 方式2: 使用gunicorn + uvicorn (生产)

```powershell
# 安装gunicorn
pip install gunicorn

# 启动服务
gunicorn app.main:app -w 4 -k uvicorn.workers.UvicornWorker -b 0.0.0.0:8000
```

#### 方式3: 使用Windows服务 (推荐生产)

创建 `windows_service.py`:

```python
import win32serviceutil
import win32service
import win32event
import servicemanager
import os
import sys

class AppService(win32serviceutil.ServiceFramework):
    _svc_name_ = "FactoryCopilot"
    _svc_display_name_ = "Factory Copilot Service"
    _svc_description_ = "AI Copilot Service for Manufacturing"

    def __init__(self, args):
        win32serviceutil.ServiceFramework.__init__(self, args)
        self.hWaitStop = win32event.CreateEvent(None, 0, 0, None)
        self.running = True

    def SvcStop(self):
        self.running = False
        win32event.SetEvent(self.hWaitStop)

    def SvcDoRun(self):
        import subprocess
        os.chdir(r"D:\code\AL.Extend.Agent\FactoryCopilot\backend")
        subprocess.Popen([
            sys.executable, "-m", "uvicorn",
            "app.main:app", "--host", "0.0.0.0", "--port", "8000"
        ])
        win32event.WaitForSingleObject(self.hWaitStop, win32event.INFINITE)

if __name__ == '__main__':
    win32serviceutil.HandleCommandLine(AppService)
```

安装服务:

```powershell
# 安装服务
python windows_service.py install

# 启动服务
python windows_service.py start

# 停止服务
python windows_service.py stop

# 卸载服务
python windows_service.py remove
```

## 三、前端部署

### 1. 安装依赖

```powershell
cd frontend

# 安装依赖
npm install
```

### 2. 配置环境变量

创建 `.env.production`:

```env
VITE_API_BASE_URL=http://your-server-ip:8000/api
```

### 3. 构建生产版本

```powershell
npm run build
```

### 4. 部署静态文件

#### 方式1: 使用Nginx (推荐)

1. 下载Nginx: http://nginx.org/en/download.html

2. 配置 `nginx.conf`:

```nginx
server {
    listen 80;
    server_name your-server-ip;

    # 前端静态文件
    location / {
        root D:/code/AL.Extend.Agent/FactoryCopilot/frontend/dist;
        index index.html;
        try_files $uri $uri/ /index.html;
    }

    # 后端API代理
    location /api {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # SSE支持
    location /api/messages/stream {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_buffering off;
        proxy_cache off;
    }
}
```

3. 启动Nginx:

```powershell
# 启动
start nginx

# 重启
nginx -s reload

# 停止
nginx -s stop
```

#### 方式2: 使用IIS

1. 安装IIS (控制面板 -> 程序 -> 启用或关闭Windows功能)

2. 创建网站:
   - 打开IIS管理器
   - 右键"网站" -> "添加网站"
   - 网站名称: FactoryCopilot
   - 物理路径: D:\code\AL.Extend.Agent\FactoryCopilot\frontend\dist
   - 端口: 80

3. 配置URL重写 (web.config):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<configuration>
  <system.webServer>
    <rewrite>
      <rules>
        <rule name="API Proxy" stopProcessing="true">
          <match url="^api/(.*)" />
          <action type="Rewrite" url="http://localhost:8000/api/{R:1}" />
        </rule>
        <rule name="SPA" stopProcessing="true">
          <match url=".*" />
          <conditions logicalGrouping="MatchAll">
            <add input="{REQUEST_FILENAME}" matchType="IsFile" negate="true" />
            <add input="{REQUEST_FILENAME}" matchType="IsDirectory" negate="true" />
          </conditions>
          <action type="Rewrite" url="/index.html" />
        </rule>
      </rules>
    </rewrite>
  </system.webServer>
</configuration>
```

## 五、启动脚本

创建 `start.bat`:

```batch
@echo off
echo Starting Factory Copilot...

:: 启动后端
cd backend
start cmd /k "venv\Scripts\activate && uvicorn app.main:app --host 0.0.0.0 --port 8000"

:: 等待后端启动
timeout /t 5

echo Services started!
echo Backend: http://localhost:8000
echo Frontend: http://localhost
pause
```

创建 `stop.bat`:

```batch
@echo off
echo Stopping Factory Copilot...

:: 停止后端
taskkill /F /IM uvicorn.exe
taskkill /F /IM python.exe

:: 停止Nginx
nginx -s stop

echo Services stopped!
pause
```

## 六、监控和日志

### 1. 日志配置

日志文件位置: `backend/logs/`

### 2. 健康检查

```powershell
# 检查后端
curl http://localhost:8000/api/health

# 检查前端
curl http://localhost
```

### 3. 性能监控

使用Windows任务管理器或Process Monitor监控资源使用情况。

## 七、备份和恢复

### 1. 数据库备份

```powershell
# SQLite备份
copy backend\data\agent.db backend\data\agent_backup_$(Get-Date -Format 'yyyyMMdd').db
```

### 2. 配置备份

```powershell
# 备份配置文件
copy backend\.env backend\.env.backup
copy frontend\.env.production frontend\.env.production.backup
```

## 八、故障排查

### 常见问题

1. **端口被占用**
```powershell
# 查看端口占用
netstat -ano | findstr :8000

# 结束进程
taskkill /F /PID <PID>
```

2. **Python模块找不到**
```powershell
# 确保虚拟环境已激活
.\venv\Scripts\activate

# 重新安装依赖
pip install -r requirements.txt
```

3. **前端无法访问后端**
- 检查CORS配置
- 检查防火墙设置
- 检查API地址配置

## 九、安全建议

1. **修改默认端口**
2. **启用HTTPS** (使用Let's Encrypt或自签名证书)
3. **配置防火墙** (只开放必要端口)
4. **定期更新依赖**
5. **使用环境变量存储敏感信息**
6. **启用日志审计**

## 十、性能优化

1. **后端优化**
   - 使用gunicorn多进程模式
   - 启用数据库连接池
   - 配置缓存

2. **前端优化**
   - 启用Gzip压缩
   - 配置CDN
   - 使用浏览器缓存

3. **数据库优化**
   - 定期清理旧数据
   - 创建索引
   - 配置WAL模式

---

部署完成后,访问 http://your-server-ip 即可使用系统!
