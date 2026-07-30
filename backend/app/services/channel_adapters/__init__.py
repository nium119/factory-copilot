"""Channel Adapter — 通知渠道抽象接口 + 实现"""
from abc import ABC, abstractmethod


async def _get_config(key: str) -> str:
    """从 DB 读取渠道配置，优先级高于 .env"""
    try:
        from app.db import get_db
        from app.models.channel_config import ChannelConfig
        from sqlalchemy import select
        async for session in get_db():
            stmt = select(ChannelConfig).where(ChannelConfig.key == key)
            result = await session.execute(stmt)
            cfg = result.scalar_one_or_none()
            if cfg:
                return cfg.value or ""
            return ""
    except Exception:
        return ""


class NotificationChannel(ABC):
    """通知渠道抽象 — 新增渠道只需实现 send()"""

    @abstractmethod
    async def send(self, notification: dict) -> bool:
        """发送通知，返回是否成功"""
        ...
