"""邮件通知 — SMTP 发送"""
import smtplib
from email.mime.text import MIMEText
from email.header import Header

from app.core.config import settings
from app.core.logger import log
from app.services.channel_adapters import NotificationChannel


class EmailChannel(NotificationChannel):
    """邮件通知 — 配置 SMTP 后启用。"""

    async def send(self, notification: dict) -> bool:
        smtp_host = getattr(settings, 'SMTP_HOST', '') or ''
        if not smtp_host:
            return False

        try:
            recipient_email = await self._get_employee_email(notification["recipient"])
            if not recipient_email:
                log.debug(f"[EmailChannel] 未找到 {notification['recipient']} 的邮箱")
                return False

            msg = MIMEText(notification["body"], "plain", "utf-8")
            msg["Subject"] = Header(notification["title"], "utf-8")
            msg["From"] = getattr(settings, 'SMTP_FROM', '') or 'ontostudio@local'
            msg["To"] = recipient_email

            # 在线程池中执行同步 SMTP
            import asyncio
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._send_smtp, msg)

            log.info(f"[EmailChannel] 发送成功 → {recipient_email}")
            return True

        except Exception as e:
            log.warning(f"[EmailChannel] 发送失败: {e}")
            return False

    def _send_smtp(self, msg: MIMEText):
        host = getattr(settings, 'SMTP_HOST', '')
        port = getattr(settings, 'SMTP_PORT', 0) or 587
        user = getattr(settings, 'SMTP_USER', '') or ''
        password = getattr(settings, 'SMTP_PASSWORD', '') or ''
        use_tls = getattr(settings, 'SMTP_USE_TLS', True)

        if port == 465:
            server = smtplib.SMTP_SSL(host, port, timeout=10)
        else:
            server = smtplib.SMTP(host, port, timeout=10)
            if use_tls:
                server.starttls()

        if user and password:
            server.login(user, password)

        server.sendmail(msg["From"], [msg["To"]], msg.as_string())
        server.quit()

    async def _get_employee_email(self, employee_code: str) -> str:
        """从 Neo4j 查询员工邮箱"""
        try:
            from app.services.neo4j_service import neo4j_service
            if not neo4j_service.connected:
                return ""
            records = await neo4j_service.execute_read(
                "MATCH (e:Employee {code: $code}) RETURN e.email AS email",
                {"code": employee_code},
            )
            if records and records[0].get("email"):
                return records[0]["email"]
        except Exception:
            pass
        return ""
