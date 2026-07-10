"""
McpServer Repository
处理 MCP 服务器配置的数据库操作
"""
from typing import List, Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mcp_server import McpServer


class McpServerRepository:
    """MCP 服务器 Repository"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_all(self) -> List[McpServer]:
        """列出所有 MCP 服务器"""
        result = await self.db.execute(select(McpServer).order_by(McpServer.name))
        return list(result.scalars().all())

    async def list_enabled(self) -> List[McpServer]:
        """列出启用的 MCP 服务器"""
        result = await self.db.execute(
            select(McpServer).where(McpServer.enabled.is_(True)).order_by(McpServer.name)
        )
        return list(result.scalars().all())

    async def get_by_name(self, name: str) -> Optional[McpServer]:
        """根据 name 获取 MCP 服务器"""
        result = await self.db.execute(select(McpServer).where(McpServer.name == name))
        return result.scalar_one_or_none()

    async def create(self, **kwargs) -> McpServer:
        """创建 MCP 服务器"""
        server = McpServer(**kwargs)
        self.db.add(server)
        await self.db.commit()
        await self.db.refresh(server)
        return server

    async def update(self, name: str, **kwargs) -> Optional[McpServer]:
        """更新 MCP 服务器"""
        server = await self.get_by_name(name)
        if not server:
            return None
        for key, value in kwargs.items():
            if hasattr(server, key):
                setattr(server, key, value)
        await self.db.commit()
        await self.db.refresh(server)
        return server

    async def delete(self, name: str) -> bool:
        """删除 MCP 服务器"""
        server = await self.get_by_name(name)
        if not server:
            return False
        await self.db.delete(server)
        await self.db.commit()
        return True
