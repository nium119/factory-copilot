"""统一数据库引擎 — 全局单例，所有模块共享。"""
import asyncio
import concurrent.futures
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession, create_async_engine
from app.core.config import settings

_engine = create_async_engine(
    settings.DATABASE_URL, echo=False,
    connect_args={"check_same_thread": False},
    pool_pre_ping=True,  # 取连接前探活：长跑后失效的连接自动重建（自愈）
)
_async_session = async_sessionmaker(_engine, expire_on_commit=False)

# 启用 WAL 模式，允许并发读写
from sqlalchemy import event
@event.listens_for(_engine.sync_engine, "connect")
def _set_wal(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


async def get_db() -> AsyncSession:
    """获取数据库会话（依赖注入或直接 async with）。"""
    async with _async_session() as session:
        yield session


def get_session() -> AsyncSession:
    """获取数据库会话（非上下文管理器，调用方需手动关闭）。"""
    return _async_session()


def run_async(coro, timeout: float = 30):
    """安全运行协程 — 自动适配同步/异步上下文。

    在同步代码中直接用 asyncio.run()；在已有事件循环中创建新线程运行。
    解决 asyncio.run() 在事件循环内报 RuntimeError 的问题。
    """
    try:
        return asyncio.run(coro)
    except RuntimeError:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result(timeout=timeout)
