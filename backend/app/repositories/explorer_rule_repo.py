"""
ExplorerRule Repository
处理异常检测规则的数据库操作
"""
from typing import List, Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.explorer_rule import ExplorerRule


class ExplorerRuleRepository:
    """异常检测规则 Repository"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_all(self) -> List[ExplorerRule]:
        """列出所有规则，按严重程度降序、名称排序"""
        result = await self.db.execute(
            select(ExplorerRule).order_by(ExplorerRule.severity.desc(), ExplorerRule.name)
        )
        return list(result.scalars().all())

    async def get_by_name(self, name: str) -> Optional[ExplorerRule]:
        """根据 name 获取规则"""
        result = await self.db.execute(select(ExplorerRule).where(ExplorerRule.name == name))
        return result.scalar_one_or_none()

    async def create(self, **kwargs) -> ExplorerRule:
        """创建规则"""
        rule = ExplorerRule(**kwargs)
        self.db.add(rule)
        await self.db.commit()
        await self.db.refresh(rule)
        return rule

    async def update(self, name: str, **kwargs) -> Optional[ExplorerRule]:
        """更新规则"""
        rule = await self.get_by_name(name)
        if not rule:
            return None
        for key, value in kwargs.items():
            if hasattr(rule, key):
                setattr(rule, key, value)
        await self.db.commit()
        await self.db.refresh(rule)
        return rule

    async def delete(self, name: str) -> bool:
        """删除规则"""
        rule = await self.get_by_name(name)
        if not rule:
            return False
        await self.db.delete(rule)
        await self.db.commit()
        return True
