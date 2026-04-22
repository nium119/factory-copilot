"""
添加会话摘要字段迁移脚本
为 conversations 表新增 summary 和 summary_version 列
"""
import sqlite3
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from app.core.config import settings


def migrate():
    # 从 DATABASE_URL 提取数据库路径
    db_url = settings.DATABASE_URL
    # sqlite+aiosqlite:///./data/agent.db -> data/agent.db
    db_path = db_url.replace("sqlite+aiosqlite:///", "").replace("sqlite:///", "")

    print(f"迁移数据库: {db_path}")

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # 检查列是否已存在
    cursor.execute("PRAGMA table_info(conversations)")
    columns = [row[1] for row in cursor.fetchall()]

    if "summary" not in columns:
        cursor.execute("ALTER TABLE conversations ADD COLUMN summary TEXT")
        print("  [OK] 已添加 summary 列")
    else:
        print("  [SKIP] summary 列已存在")

    if "summary_version" not in columns:
        cursor.execute("ALTER TABLE conversations ADD COLUMN summary_version INTEGER DEFAULT 0")
        print("  [OK] 已添加 summary_version 列")
    else:
        print("  [SKIP] summary_version 列已存在")

    conn.commit()
    conn.close()
    print("迁移完成")


if __name__ == "__main__":
    migrate()
