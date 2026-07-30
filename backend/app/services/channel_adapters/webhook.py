"""通用 Webhook 通知 — 推送到任意自定义 URL"""
import httpx

from app.core.config import settings
from app.core.logger import log
from app.services.channel_adapters import NotificationChannel, _get_config


class WebhookChannel(NotificationChannel):
    """通用 Webhook — 配置 WEBHOOK_URL 后启用。

    向指定 URL POST JSON：
    {
      "title": "...",
      "body": "...",
      "recipient": "EMP001",
      "severity": "warning",
      "type": "plan.generated",
      "created_at": "..."
    }
    """

    async def send(self, notification: dict) -> bool:
        webhook_url = await _get_config("webhook_url") or settings.WEBHOOK_URL
        if not webhook_url:
            return False

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(webhook_url, json={
                    "title": notification.get("title", ""),
                    "body": notification.get("body", ""),
                    "recipient": notification.get("recipient", ""),
                    "severity": notification.get("severity", ""),
                    "type": notification.get("type", ""),
                    "source": notification.get("source", ""),
                    "created_at": notification.get("created_at", ""),
                })
                ok = resp.status_code in (200, 201, 204)
                if ok:
                    log.info(f"[WebhookChannel] 发送成功 → {webhook_url}")
                else:
                    log.warning(f"[WebhookChannel] 发送失败 {resp.status_code}: {resp.text[:200]}")
                return ok

        except Exception as e:
            log.warning(f"[WebhookChannel] 发送异常: {e}")
            return False
