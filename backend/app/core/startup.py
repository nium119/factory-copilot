"""启动时自动初始化数据库 — 建表 + Agent 种子数据（仅首次）"""
import os

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
from app.models.api_log import ApiCallLog  # noqa: F401
from app.models.chain import Chain, ChainStep  # noqa: F401
from app.models.namespace_config import NamespaceConfig  # noqa: F401
from app.models.skill_embedding import SkillEmbedding  # noqa: F401
from app.models.mcp_server import McpServer  # noqa: F401
from app.models.a2a_agent import A2aAgent  # noqa: F401
from app.models.intent_feedback import IntentFeedback  # noqa: F401
from app.models.event import EventQueue  # noqa: F401
from app.models.notification import Notification, NotificationRule  # noqa: F401


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
        # 兼容旧表：加新列
        try:
            await conn.run_sync(lambda c: c.exec_driver_sql("ALTER TABLE agent_agents ADD COLUMN project_description TEXT DEFAULT ''"))
        except Exception:
            pass
        try:
            await conn.run_sync(lambda c: c.exec_driver_sql("ALTER TABLE agent_agents ADD COLUMN namespace VARCHAR(64) DEFAULT ''"))
        except Exception:
            pass
        try:
            await conn.run_sync(lambda c: c.exec_driver_sql("ALTER TABLE agent_skill_embeddings ADD COLUMN namespace VARCHAR(64) DEFAULT 'default'"))
        except Exception:
            pass
        # BM25 FTS5 索引表
        await conn.run_sync(lambda c: c.exec_driver_sql(
            "CREATE VIRTUAL TABLE IF NOT EXISTS agent_skill_fts USING fts5(skill_name, namespace, content, tokenize='unicode61')"
        ))
        # 数据迁移：从旧表名迁移数据到新表名
        await conn.run_sync(_do_migrate)
    await engine.dispose()
    log.info("[DB] 所有表已就绪")

    await _seed_agents_if_empty()


def _do_migrate(c):
    """将旧表名数据迁移到新表名（agent_ 前缀），然后删除旧表"""
    table_map = {
        "agents": "agent_agents", "conversations": "agent_conversations",
        "messages": "agent_messages", "feedbacks": "agent_feedbacks",
        "alerts": "agent_alerts", "user_preferences": "agent_user_preferences",
        "api_call_logs": "agent_api_call_logs", "namespace_configs": "agent_namespace_configs",
        "chains": "agent_chains", "chain_steps": "agent_chain_steps",
        "kpi_thresholds": "agent_kpi_thresholds",
        "mcp_servers": "agent_mcp_servers", "a2a_agents": "agent_a2a_agents",
        "conversation_memory": "agent_conversation_memory",
    }
    tables = {row[0] for row in c.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    for old_name, new_name in table_map.items():
        if old_name not in tables or new_name not in tables:
            continue
        if c.exec_driver_sql(f"SELECT COUNT(*) FROM \"{new_name}\"").fetchone()[0] > 0:
            continue
        old_cols = [cr[1] for cr in c.exec_driver_sql(f"PRAGMA table_info(\"{old_name}\")").fetchall()]
        new_cols = {cr[1] for cr in c.exec_driver_sql(f"PRAGMA table_info(\"{new_name}\")").fetchall()}
        cols = [c for c in old_cols if c in new_cols]
        if not cols:
            continue
        cols_str = ", ".join(f"\"{col}\"" for col in cols)
        try:
            c.exec_driver_sql(f"INSERT INTO \"{new_name}\" ({cols_str}) SELECT {cols_str} FROM \"{old_name}\"")
            count = c.exec_driver_sql(f"SELECT COUNT(*) FROM \"{new_name}\"").fetchone()[0]
            c.exec_driver_sql(f"DROP TABLE \"{old_name}\"")
            log.info(f"[DB] 迁移: {old_name} → {new_name} ({count} 条)")
        except Exception as e:
            log.warning(f"[DB] 迁移 {old_name} 跳过: {e}")


async def _seed_agents_if_empty():
    from app.db import get_db
    from app.repositories.agent_repository import AgentRepository
    from app.agents.agent_config import AGENT_DEFINITIONS

    async for session in get_db():
        repo = AgentRepository(session)
        all_agents = await repo.get_all()
        existing = {a.name for a in all_agents}
        defined = set(AGENT_DEFINITIONS.keys())

        # 清除旧 Agent
        stale = existing - defined
        for name in stale:
            await repo.delete(name)

        # 插入新 Agent
        inserted = 0
        for name, meta in AGENT_DEFINITIONS.items():
            if name in existing:
                continue
            await repo.create(
                name=name, display_name=meta.get("display_name", ""),
                icon=meta.get("icon", ""), color=meta.get("color", "#6c5ce7"),
                description=meta.get("description", ""),
                enabled=meta.get("enabled", True),
                roles=meta.get("roles", []),
                keywords=meta.get("keywords", []),
                sort_order=meta.get("sort_order", 0),
            )
            inserted += 1

        if inserted or stale:
            log.info(f"[DB] Agent 种子: +{inserted} 新增, -{len(stale)} 移除")
