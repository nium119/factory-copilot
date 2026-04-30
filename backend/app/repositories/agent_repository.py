"""
Agent Repository
处理 Agent 配置的数据库操作
"""
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent


class AgentRepository:
    """Agent 配置 Repository"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_name(self, name: str) -> Optional[Agent]:
        """根据 name 获取 Agent"""
        result = await self.db.execute(select(Agent).where(Agent.name == name))
        return result.scalar_one_or_none()

    async def get_enabled_agents(self, roles: Optional[List[str]] = None) -> List[Agent]:
        """获取启用的 Agent，可按角色过滤"""
        query = select(Agent).where(Agent.enabled.is_(True)).order_by(Agent.sort_order.desc())
        result = await self.db.execute(query)
        agents = list(result.scalars().all())
        # 如果没有传 roles 或 Agent 的 roles 为空，则返回所有启用的 Agent
        if not roles:
            return agents
        return [a for a in agents if not a.roles or any(r in roles for r in a.roles)]

    async def get_all(self) -> List[Agent]:
        """获取所有 Agent（含未启用的）"""
        result = await self.db.execute(select(Agent).order_by(Agent.sort_order.desc()))
        return list(result.scalars().all())

    async def create(self, **kwargs) -> Agent:
        """创建 Agent"""
        agent = Agent(**kwargs)
        self.db.add(agent)
        await self.db.commit()
        await self.db.refresh(agent)
        return agent

    async def update(self, name: str, **kwargs) -> Optional[Agent]:
        """更新 Agent"""
        agent = await self.get_by_name(name)
        if not agent:
            return None
        for key, value in kwargs.items():
            if hasattr(agent, key):
                setattr(agent, key, value)
        await self.db.commit()
        await self.db.refresh(agent)
        return agent

    async def delete(self, name: str) -> bool:
        """删除 Agent"""
        agent = await self.get_by_name(name)
        if not agent:
            return False
        await self.db.delete(agent)
        await self.db.commit()
        return True
