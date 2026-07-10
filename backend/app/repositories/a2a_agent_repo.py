"""
A2aAgent Repository
处理 A2A 外部 Agent 配置的数据库操作
"""
from typing import List, Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.a2a_agent import A2aAgent


class A2aAgentRepository:
    """A2A Agent Repository"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_all(self) -> List[A2aAgent]:
        """列出所有 A2A Agent"""
        result = await self.db.execute(select(A2aAgent).order_by(A2aAgent.name))
        return list(result.scalars().all())

    async def list_enabled(self) -> List[A2aAgent]:
        """列出启用的 A2A Agent"""
        result = await self.db.execute(
            select(A2aAgent).where(A2aAgent.enabled.is_(True)).order_by(A2aAgent.name)
        )
        return list(result.scalars().all())

    async def get_by_name(self, name: str) -> Optional[A2aAgent]:
        """根据 name 获取 A2A Agent"""
        result = await self.db.execute(select(A2aAgent).where(A2aAgent.name == name))
        return result.scalar_one_or_none()

    async def create(self, **kwargs) -> A2aAgent:
        """创建 A2A Agent"""
        agent = A2aAgent(**kwargs)
        self.db.add(agent)
        await self.db.commit()
        await self.db.refresh(agent)
        return agent

    async def update(self, name: str, **kwargs) -> Optional[A2aAgent]:
        """更新 A2A Agent"""
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
        """删除 A2A Agent"""
        agent = await self.get_by_name(name)
        if not agent:
            return False
        await self.db.delete(agent)
        await self.db.commit()
        return True
