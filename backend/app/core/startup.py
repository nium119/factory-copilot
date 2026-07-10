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
from app.models.explorer_rule import ExplorerRule  # noqa: F401
from app.models.kpi_threshold import KpiThreshold  # noqa: F401
from app.models.mcp_server import McpServer  # noqa: F401
from app.models.a2a_agent import A2aAgent  # noqa: F401


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
        await conn.run_sync(lambda c: c.exec_driver_sql("ALTER TABLE agents ADD COLUMN project_description TEXT DEFAULT ''"))
    except Exception:
        pass
    await engine.dispose()
    log.info("[DB] 所有表已就绪")

    await _seed_agents_if_empty()


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
