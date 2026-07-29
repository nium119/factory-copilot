"""Channel Adapter — 通知渠道抽象接口 + 实现"""
from abc import ABC, abstractmethod


class NotificationChannel(ABC):
    """通知渠道抽象 — 新增渠道只需实现 send()"""

    @abstractmethod
    async def send(self, notification: dict) -> bool:
        """发送通知，返回是否成功"""
        ...
