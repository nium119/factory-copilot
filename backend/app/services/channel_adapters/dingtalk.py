"""钉钉通知 — 群机器人 Webhook 推送"""
import httpx

from app.core.config import settings
from app.core.logger import log
from app.services.channel_adapters import NotificationChannel, _get_config


class DingTalkChannel(NotificationChannel):
    """钉钉群机器人 Webhook — 配置 DINGTALK_WEBHOOK_URL 后启用。"""

    async def send(self, notification: dict) -> bool:
        webhook_url = await _get_config("dingtalk_webhook") or settings.DINGTALK_WEBHOOK_URL
        if not webhook_url:
            return False

        try:
            content = (
                f"【{notification.get('severity_label', '通知')}】{notification['title']}\n"
                f"{notification['body']}\n"
                f"时间：{notification.get('created_at', '')}"
            )

            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(webhook_url, json={
                    "msgtype": "text",
                    "text": {"content": content},
                })
                if resp.status_code == 200:
                    log.info(f"[DingTalkChannel] 发送成功")
                    return True
                log.warning(f"[DingTalkChannel] 发送失败 {resp.status_code}: {resp.text}")
                return False

        except Exception as e:
            log.warning(f"[DingTalkChannel] 发送异常: {e}")
            return False
