"""A2aApiKey Repository — A2A 服务端 API Key 数据库操作"""
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.a2a_api_key import A2aApiKey


class A2aApiKeyRepository:
    """A2A 服务端 API Key Repository"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_all(self) -> List[A2aApiKey]:
        """列出所有 API Key"""
        result = await self.db.execute(select(A2aApiKey).order_by(A2aApiKey.name))
        return list(result.scalars().all())

    async def get_by_name(self, name: str) -> Optional[A2aApiKey]:
        """按备注名获取 Key"""
        result = await self.db.execute(select(A2aApiKey).where(A2aApiKey.name == name))
        return result.scalar_one_or_none()

    async def get_by_hash(self, key_hash: str) -> Optional[A2aApiKey]:
        """按 key hash 获取（验签用）"""
        result = await self.db.execute(select(A2aApiKey).where(A2aApiKey.key_hash == key_hash))
        return result.scalar_one_or_none()

    async def create(self, **kwargs) -> A2aApiKey:
        """创建 API Key"""
        obj = A2aApiKey(**kwargs)
        self.db.add(obj)
        await self.db.commit()
        await self.db.refresh(obj)
        return obj

    async def update(self, name: str, **kwargs) -> Optional[A2aApiKey]:
        """更新 API Key"""
        obj = await self.get_by_name(name)
        if not obj:
            return None
        for key, value in kwargs.items():
            if hasattr(obj, key):
                setattr(obj, key, value)
        await self.db.commit()
        await self.db.refresh(obj)
        return obj

    async def delete(self, name: str) -> bool:
        """吊销 API Key"""
        obj = await self.get_by_name(name)
        if not obj:
            return False
        await self.db.delete(obj)
        await self.db.commit()
        return True
