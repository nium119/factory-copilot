"""统一数据库引擎 — 全局单例，所有模块共享。"""
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession, create_async_engine
from app.core.config import settings

_engine = create_async_engine(settings.DATABASE_URL, echo=False)
_async_session = async_sessionmaker(_engine, expire_on_commit=False)


async def get_db() -> AsyncSession:
    """获取数据库会话（依赖注入或直接 async with）。"""
    async with _async_session() as session:
        yield session


def get_session() -> AsyncSession:
    """获取数据库会话（非上下文管理器，调用方需手动关闭）。"""
    return _async_session()
