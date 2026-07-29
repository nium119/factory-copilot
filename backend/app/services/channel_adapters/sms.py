"""短信通知 — 阿里云/腾讯云 SMS 网关"""
import json
from urllib.parse import urlencode

import httpx

from app.core.config import settings
from app.core.logger import log
from app.services.channel_adapters import NotificationChannel


class SMSChannel(NotificationChannel):
    """短信通知 — 配置 SMS_PROVIDER + SMS_API_URL 后启用。

    支持 provider: aliyun | tencent | generic
    generic 模式直接 POST JSON 到 SMS_API_URL，payload 中包含 phone 和 content 字段。
    """

    async def send(self, notification: dict) -> bool:
        provider = (getattr(settings, 'SMS_PROVIDER', '') or '').strip()
        api_url = (getattr(settings, 'SMS_API_URL', '') or '').strip()
        if not api_url:
            return False

        try:
            phone = await self._get_employee_phone(notification["recipient"])
            if not phone:
                log.debug(f"[SMSChannel] 未找到 {notification['recipient']} 的手机号")
                return False

            content = f"{notification['title']}"

            if provider in ('aliyun', 'tencent'):
                key = settings.SMS_API_KEY or ''
                secret = settings.SMS_API_SECRET or ''
                sign = settings.SMS_SIGN_NAME or ''
                template = settings.SMS_TEMPLATE_CODE or ''
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.post(api_url, json={
                        "phone": phone,
                        "content": content,
                        "sign_name": sign,
                        "template_code": template,
                        "api_key": key,
                        "api_secret": secret,
                    })
            else:
                # generic — 直接 POST，由网关适配
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.post(api_url, json={
                        "phone": phone,
                        "content": content,
                        "api_key": settings.SMS_API_KEY or '',
                    })

            ok = resp.status_code == 200
            if ok:
                log.info(f"[SMSChannel] 发送成功 → {phone}")
            else:
                log.warning(f"[SMSChannel] 发送失败 {resp.status_code}: {resp.text[:200]}")
            return ok

        except Exception as e:
            log.warning(f"[SMSChannel] 发送异常: {e}")
            return False

    async def _get_employee_phone(self, employee_code: str) -> str:
        try:
            from app.services.neo4j_service import neo4j_service
            if not neo4j_service.connected:
                return ""
            records = await neo4j_service.execute_read(
                "MATCH (e:Employee {code: $code}) RETURN e.phone AS phone",
                {"code": employee_code},
            )
            if records and records[0].get("phone"):
                return records[0]["phone"]
        except Exception:
            pass
        return ""
