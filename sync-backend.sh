#!/bin/bash
# 同步后端 Python 代码到服务器并重启容器
# 用法: bash sync-backend.sh

SERVER="root@172.21.10.22"
BACKEND_DIR="/d/code/long-running-agent-harness/projects/factory-copilot/backend/app"
REMOTE_DIR="/home/websites/factory-copilot/backend/app"

echo "=== 同步后端代码 ==="
scp -r -o StrictHostKeyChecking=no \
  "$BACKEND_DIR"/* "$SERVER:$REMOTE_DIR/"

echo "=== 同步 MCP Server（OntoStudio 本体只读 MCP 副本） ==="
ssh "$SERVER" "mkdir -p /home/websites/factory-copilot/backend/mcp"
scp -o StrictHostKeyChecking=no \
  /d/code/long-running-agent-harness/projects/factory-copilot/backend/mcp/*.py \
  "$SERVER:/home/websites/factory-copilot/backend/mcp/"

echo "=== 清除 pycache 并重启容器 ==="
ssh "$SERVER" "find /home/websites/factory-copilot/backend/app -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null; docker restart factory-copilot && echo '重启完成'"
