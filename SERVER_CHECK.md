# 服务器访问问题排查清单

## 服务器地址
http://47.103.96.248:9004

## 排查步骤

### 1. 检查服务是否启动

在服务器上运行:
```powershell
# 检查端口是否被监听
netstat -ano | findstr :9004

# 检查Python进程是否运行
tasklist | findstr python
```

### 2. 检查防火墙

**Windows防火墙**:
```powershell
# 查看防火墙状态
netsh advfirewall show allprofiles

# 添加入站规则允许9004端口
netsh advfirewall firewall add rule name="Agent API" dir=in action=allow protocol=tcp localport=9004

# 添加出站规则
netsh advfirewall firewall add rule name="Agent API" dir=out action=allow protocol=tcp localport=9004
```

**云服务器安全组**:
- 登录云服务器控制台(阿里云/腾讯云等)
- 找到安全组设置
- 添加入站规则: 端口9004, 协议TCP, 源地址0.0.0.0/0
- 添加出站规则: 端口9004, 协议TCP

### 3. 检查服务绑定地址

确保服务绑定到 `0.0.0.0:9004` 而不是 `127.0.0.1:9004`:

```powershell
# 正确的启动方式
uvicorn app.main:app --host 0.0.0.0 --port 9004

# 错误的启动方式(只能本地访问)
uvicorn app.main:app --host 127.0.0.1 --port 9004
```

### 4. 测试本地访问

在服务器上测试:
```powershell
# 测试本地访问
curl http://localhost:9004
curl http://127.0.0.1:9004

# 或使用PowerShell
Invoke-WebRequest -Uri http://localhost:9004
```

### 5. 检查日志

查看服务启动日志:
```powershell
# 查看是否有错误
cd D:\code\long-running-agent-harness\projects\factory-copilot\backend
type logs\app.log
```

### 6. 检查进程

```powershell
# 查看Python进程详情
wmic process where "name='python.exe'" get commandline
```

## 常见问题

### 问题1: 端口被占用
```powershell
# 查找占用端口的进程
netstat -ano | findstr :9004
# 结束进程
taskkill /F /PID <PID>
```

### 问题2: 防火墙阻止
- 临时关闭防火墙测试(不推荐生产环境)
- 添加防火墙规则允许9004端口

### 问题3: 云服务器安全组未配置
- 必须在云控制台配置安全组
- 仅配置本地防火墙不够

### 问题4: 服务未启动
- 检查start.bat是否成功运行
- 检查是否有错误日志

## 验证步骤

### 1. 本地验证
在服务器上:
```powershell
curl http://localhost:9004/health
```

### 2. 外网验证
在本地电脑上:
```powershell
curl http://47.103.96.248:9004/health
```

### 3. 浏览器验证
直接访问: http://47.103.96.248:9004

## 快速修复命令

```powershell
# 1. 停止现有服务
taskkill /F /IM python.exe

# 2. 添加防火墙规则
netsh advfirewall firewall add rule name="Agent API" dir=in action=allow protocol=tcp localport=9004

# 3. 重新启动服务
cd D:\code\long-running-agent-harness\projects\factory-copilot
start.bat
```

## 预期结果

访问 http://47.103.96.248:9004 应该看到:
- 前端界面(如果配置了静态文件服务)
- 或API文档页面

访问 http://47.103.96.248:9004/health 应该返回:
```json
{"status": "ok"}
```
