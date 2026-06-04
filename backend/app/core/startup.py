"""启动时自动初始化数据库 — 建表 + Agent 种子数据（仅首次）"""
import json
import os
import sqlite3

from sqlalchemy.ext.asyncio import create_async_engine

from app.core.logger import log
from app.models.base import Base

# 注册 ORM 模型到 Base.metadata（触发 create_all 建表）
from app.models.agent import Agent  # noqa: F401
from app.models.alert import Alert  # noqa: F401
from app.models.conversation import Conversation  # noqa: F401
from app.models.feedback import Feedback  # noqa: F401
from app.models.message import Message  # noqa: F401
from app.models.user_preference import UserPreference  # noqa: F401


DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "data", "agent.db"
)


async def ensure_database():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    db_url = f"sqlite+aiosqlite:///{DB_PATH}"
    engine = create_async_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()
    log.info("[DB] 所有表已就绪")

    _seed_agents_if_empty()


def _seed_agents_if_empty():
    from app.agents.agent_config import AGENT_DEFINITIONS

    conn = sqlite3.connect(DB_PATH)
    existing = {r[0] for r in conn.execute("SELECT name FROM agents").fetchall()}
    defined = set(AGENT_DEFINITIONS.keys())

    # 清除不在 AGENT_DEFINITIONS 中的旧 Agent（如 10→4 合并后的残留）
    stale = existing - defined
    if stale:
        for name in stale:
            conn.execute("DELETE FROM agents WHERE name = ?", (name,))
        log.info(f"[DB] 已移除 {len(stale)} 个旧 Agent: {stale}")

    # 插入或跳过已存在的
    now = __import__("datetime").datetime.now().isoformat()
    inserted = 0
    for name, meta in AGENT_DEFINITIONS.items():
        if name in existing:
            continue
        conn.execute("""
            INSERT INTO agents (name, display_name, icon, color, description, enabled, roles, keywords, sort_order, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (
            name, meta["display_name"], meta["icon"], meta["color"],
            meta["description"], int(meta.get("enabled", True)),
            json.dumps(meta.get("roles", [])),
            json.dumps(meta.get("keywords", [])) if "keywords" in meta else "[]",
            meta.get("sort_order", 0), now, now,
        ))
        inserted += 1

    conn.commit()
    conn.close()
    if inserted or stale:
        log.info(f"[DB] Agent 种子: +{inserted} 新增, -{len(stale)} 移除")
