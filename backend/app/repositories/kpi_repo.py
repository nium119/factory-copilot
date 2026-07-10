"""
KpiThreshold Repository
处理 KPI 阈值的数据库操作
"""
from typing import List, Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.kpi_threshold import KpiThreshold


class KpiThresholdRepository:
    """KPI 阈值 Repository"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_all(self) -> List[KpiThreshold]:
        """列出所有 KPI，按域和 key 排序"""
        result = await self.db.execute(
            select(KpiThreshold).order_by(KpiThreshold.domain, KpiThreshold.kpi_key)
        )
        return list(result.scalars().all())

    async def get_by_key(self, kpi_key: str) -> Optional[KpiThreshold]:
        """根据 kpi_key 获取 KPI"""
        result = await self.db.execute(select(KpiThreshold).where(KpiThreshold.kpi_key == kpi_key))
        return result.scalar_one_or_none()

    async def create(self, **kwargs) -> KpiThreshold:
        """创建 KPI"""
        kpi = KpiThreshold(**kwargs)
        self.db.add(kpi)
        await self.db.commit()
        await self.db.refresh(kpi)
        return kpi

    async def update(self, kpi_key: str, **kwargs) -> Optional[KpiThreshold]:
        """更新 KPI"""
        kpi = await self.get_by_key(kpi_key)
        if not kpi:
            return None
        for key, value in kwargs.items():
            if hasattr(kpi, key):
                setattr(kpi, key, value)
        await self.db.commit()
        await self.db.refresh(kpi)
        return kpi

    async def delete(self, kpi_key: str) -> bool:
        """删除 KPI"""
        kpi = await self.get_by_key(kpi_key)
        if not kpi:
            return False
        await self.db.delete(kpi)
        await self.db.commit()
        return True
