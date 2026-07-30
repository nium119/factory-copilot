#!/bin/bash
# Factory Copilot 数据备份 — 定时执行（cron: 0 3 * * *）
set -e

BACKUP_DIR="./backups"
DB_FILE="./data/agent.db"
RETENTION_DAYS=30

mkdir -p "$BACKUP_DIR"

DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="$BACKUP_DIR/fc_backup_$DATE.tar.gz"

# 打包备份
tar -czf "$BACKUP_FILE" "$DB_FILE" 2>/dev/null

# 清理过期备份
find "$BACKUP_DIR" -name "fc_backup_*.tar.gz" -mtime +$RETENTION_DAYS -delete

echo "[$(date)] Backup: $BACKUP_FILE ($(du -h "$BACKUP_FILE" | cut -f1))"
