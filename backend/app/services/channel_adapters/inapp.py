"""应用内通知 — 写入 notifications 表 + EventBus 实时推送"""
import json
from datetime import datetime

from app.core.logger import log
from app.services.channel_adapters import NotificationChannel
from app.services.event_bus import event_bus


class InAppChannel(NotificationChannel):
    """应用内通知 — 持久化到数据库并推送前端 SSE。"""

    async def send(self, notification: dict) -> bool:
        try:
            from app.models.notification import Notification
            from app.db import get_db

            async for session in get_db():
                record = Notification(
                    recipient=notification["recipient"],
                    type=notification.get("type", "info"),
                    severity=notification.get("severity", "info"),
                    title=notification["title"],
                    body=notification["body"],
                    channel="inapp",
                    status="unread",
                    source=notification.get("source", ""),
                    ref_conversation_id=notification.get("ref_conversation_id", ""),
                    ref_chain_id=notification.get("ref_chain_id", ""),
                    ref_plan_id=notification.get("ref_plan_id", ""),
                    action_data=json.dumps(notification.get("action_data")) if notification.get("action_data") else None,
                )
                session.add(record)
                await session.commit()

                # 实时推送给接收人
                await event_bus.publish("notification", {
                    "id": record.id,
                    "recipient": record.recipient,
                    "title": record.title,
                    "body": record.body,
                    "severity": record.severity,
                    "type": record.type,
                    "created_at": record.created_at.isoformat() if record.created_at else "",
                })
                return True

        except Exception as e:
            log.warning(f"[InAppChannel] 发送失败: {e}")
            return False
