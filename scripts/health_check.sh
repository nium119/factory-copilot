#!/bin/bash
# 健康检查 — 监控系统定时调用
# 用法: ./health_check.sh [url]

URL="${1:-http://127.0.0.1:9001/api/system/health}"

RESP=$(curl -s "$URL" 2>/dev/null)
OK=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('ok',False))" 2>/dev/null)

if [ "$OK" = "True" ]; then
    echo "[OK] $(date) — 系统正常"
    exit 0
else
    echo "[FAIL] $(date) — 系统异常: $RESP"
    exit 1
fi
