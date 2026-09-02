#!/bin/bash
# 一键同步到服务器
# 用法: ./sync.sh

set -e
SERVER="root@172.21.10.22"
PROJ="/home/websites/factory-copilot"
DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== 1/4 构建前端 ==="
cd "$DIR/frontend" && npm run build

echo "=== 2/4 打包 ==="
# 显式用 Git Bash 自带的 GNU tar，避免被 Windows 自带 bsdtar 抢占（bsdtar 不认 /tmp 路径）
TAR_BIN=/usr/bin/tar
cd "$DIR/frontend" && "$TAR_BIN" -czf /tmp/dist.tar.gz -C . dist/
cd "$DIR/backend" && "$TAR_BIN" -czf /tmp/fc_app.tar.gz app/ mcp/

echo "=== 3/4 上传 ==="
scp /tmp/dist.tar.gz /tmp/fc_app.tar.gz $SERVER:/tmp/

echo "=== 4/4 部署 ==="
ssh $SERVER "cd $PROJ && \
  rm -rf frontend/dist && \
  tar -xzf /tmp/dist.tar.gz -C frontend/ && \
  tar -xzf /tmp/fc_app.tar.gz -C backend/ && \
  find backend/app -name '__pycache__' -exec rm -rf {} + 2>/dev/null; \
  docker restart factory-copilot && \
  sleep 10 && \
  curl -s http://127.0.0.1:9004/health"

echo ""
echo "=== 完成 ==="
