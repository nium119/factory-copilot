"""
Agent 表初始化迁移脚本
创建 agents 表并插入默认 Agent 数据（从 agent_config 读取元数据）
"""
import json
import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "agent.db")

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS agents (
    name TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    icon TEXT NOT NULL DEFAULT '🤖',
    color TEXT DEFAULT '#6c5ce7',
    description TEXT DEFAULT '',
    enabled BOOLEAN NOT NULL DEFAULT 1,
    roles TEXT DEFAULT '[]',
    keywords TEXT DEFAULT '[]',
    system_prompt TEXT,
    sort_order INTEGER DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""


def run_migration():
    from app.agents.agent_config import AGENT_DEFINITIONS

    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(CREATE_TABLE_SQL)

    for name, meta in AGENT_DEFINITIONS.items():
        cursor.execute("SELECT name FROM agents WHERE name = ?", (name,))
        if cursor.fetchone() is None:
            cursor.execute(
                """INSERT INTO agents (name, display_name, icon, color, description, enabled, roles, keywords, sort_order)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    name,
                    meta["display_name"],
                    meta["icon"],
                    meta["color"],
                    meta["description"],
                    int(meta.get("enabled", True)),
                    json.dumps(meta.get("roles", [])),
                    json.dumps(meta.get("keywords", [])),
                    meta.get("sort_order", 0),
                ),
            )
            print(f"  Inserted agent: {meta['display_name']}")
        else:
            print(f"  Agent already exists: {meta['display_name']}")

    conn.commit()
    conn.close()
    print("Agent migration completed.")


if __name__ == "__main__":
    run_migration()
