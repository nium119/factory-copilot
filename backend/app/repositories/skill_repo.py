"""动态 Skill Repository"""
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.dynamic_skill import DynamicSkill


class SkillRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_enabled(self) -> list[DynamicSkill]:
        result = await self.db.execute(
            select(DynamicSkill).where(DynamicSkill.enabled.is_(True))
        )
        return list(result.scalars().all())

    async def list_all(self) -> list[DynamicSkill]:
        result = await self.db.execute(select(DynamicSkill))
        return list(result.scalars().all())

    async def get_by_name(self, name: str) -> Optional[DynamicSkill]:
        result = await self.db.execute(select(DynamicSkill).where(DynamicSkill.name == name))
        return result.scalar_one_or_none()

    async def create(self, **kwargs) -> DynamicSkill:
        skill = DynamicSkill(**kwargs)
        self.db.add(skill)
        await self.db.commit()
        await self.db.refresh(skill)
        return skill

    async def update(self, name: str, **kwargs) -> Optional[DynamicSkill]:
        skill = await self.get_by_name(name)
        if not skill:
            return None
        for key, value in kwargs.items():
            if hasattr(skill, key):
                setattr(skill, key, value)
        await self.db.commit()
        await self.db.refresh(skill)
        return skill

    async def delete(self, name: str) -> bool:
        skill = await self.get_by_name(name)
        if not skill:
            return False
        await self.db.delete(skill)
        await self.db.commit()
        return True
