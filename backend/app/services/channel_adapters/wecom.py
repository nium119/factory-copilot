"""企业微信通知 — POST webhook 机器人消息"""
import httpx

from app.core.config import settings
from app.core.logger import log
from app.services.channel_adapters import NotificationChannel, _get_config


class WeComChannel(NotificationChannel):
    """企业微信机器人 Webhook 推送 — 配置 WECOM_WEBHOOK_URL 后启用。"""

    async def send(self, notification: dict) -> bool:
        webhook_url = await _get_config("wecom_webhook") or settings.WECOM_WEBHOOK_URL
        if not webhook_url:
            log.debug("[WeComChannel] 未配置 WECOM_WEBHOOK_URL，跳过")
            return False

        try:
            # 查询接收人信息（从 Neo4j 拿姓名和工号）
            recipient_info = await self._get_employee_name(notification["recipient"])

            content = (
                f"【{notification.get('severity_label', '通知')}】{notification['title']}\n"
                f"接收人：{recipient_info}\n"
                f"{notification['body']}\n"
                f"时间：{notification.get('created_at', '')}"
            )

            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(webhook_url, json={
                    "msgtype": "text",
                    "text": {"content": content},
                })
                if resp.status_code == 200:
                    log.info(f"[WeComChannel] 发送成功 → {notification['recipient']}")
                    return True
                else:
                    log.warning(f"[WeComChannel] 发送失败 {resp.status_code}: {resp.text}")
                    return False

        except Exception as e:
            log.warning(f"[WeComChannel] 发送异常: {e}")
            return False

    async def _get_employee_name(self, employee_code: str) -> str:
        """从 Neo4j 查询员工姓名"""
        try:
            from app.services.neo4j_service import neo4j_service

            if not neo4j_service.connected:
                return employee_code

            records = await neo4j_service.execute_read(
                "MATCH (e:Employee {code: $code}) RETURN e.name AS name",
                {"code": employee_code},
            )
            if records:
                name = records[0].get("name", "")
                return f"{name}（{employee_code}）" if name else employee_code
            return employee_code

        except Exception:
            return employee_code
