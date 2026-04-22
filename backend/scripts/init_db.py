"""
初始化数据库
创建所有表
"""
import asyncio
import sys
import os

# 添加项目路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from sqlalchemy.ext.asyncio import create_async_engine
from app.models.base import Base
from app.models.conversation import Conversation
from app.models.message import Message
from app.core.config import settings


async def create_tables():
    """创建所有表"""
    print(f"Creating tables for database: {settings.DATABASE_URL}")

    engine = create_async_engine(settings.DATABASE_URL, echo=True)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    print("✅ Tables created successfully!")


if __name__ == "__main__":
    asyncio.run(create_tables())
