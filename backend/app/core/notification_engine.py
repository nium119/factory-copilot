"""通知引擎 — 规则匹配 + 接收人解析 + 渠道分发"""
import json
import re

from app.core.config import settings
from app.core.logger import log


class NotificationEngine:
    """规则引擎：事件 → 匹配规则 → 解析接收人 → 分发通知"""

    async def process_event(self, event_type: str, payload: dict) -> int:
        """处理一个事件，返回创建的通知数。"""
        rules = await self._load_rules(event_type)
        if not rules:
            return 0

        total = 0
        for rule in rules:
            try:
                if not self._evaluate_condition(rule.get("condition"), payload):
                    continue

                recipients = await self._resolve_recipients(
                    rule["target"], payload
                )

                for recipient in recipients:
                    notification = self._build_notification(rule, payload, recipient)
                    channels = json.loads(rule.get("channels", '["inapp"]'))

                    for channel_name in channels:
                        adapter = self._get_adapter(channel_name)
                        if adapter:
                            ok = await adapter.send(notification)
                            if ok:
                                total += 1

            except Exception as e:
                log.warning(f"[NotificationEngine] 规则 {rule.get('id')} 处理异常: {e}")

        return total

    async def _load_rules(self, event_type: str) -> list[dict]:
        """从数据库加载匹配的启用规则"""
        try:
            from app.models.notification import NotificationRule
            from app.db import get_db

            async for session in get_db():
                from sqlalchemy import select
                stmt = (
                    select(NotificationRule)
                    .where(
                        NotificationRule.event_type == event_type,
                        NotificationRule.enabled == True,  # noqa: E712
                    )
                    .order_by(NotificationRule.priority.desc())
                )
                result = await session.execute(stmt)
                rules = result.scalars().all()
                return [
                    {
                        "id": r.id,
                        "event_type": r.event_type,
                        "condition": r.condition or "",
                        "target": r.target,
                        "channels": r.channels,
                        "title_template": r.title_template,
                        "body_template": r.body_template,
                    }
                    for r in rules
                ]
            return []
        except Exception as e:
            log.warning(f"[NotificationEngine] 加载规则失败: {e}")
            return []

    def _evaluate_condition(self, condition: str, payload: dict) -> bool:
        """评估 JSONPath 风格的条件表达式。空条件视为 True。

        支持格式:
          - 空字符串 → True
          - $.field > 0    → payload["field"] > 0
          - $.field.length > 0 → len(payload["field"]) > 0
          - $.field != "ok" → payload["field"] != "ok"
        """
        if not condition or not condition.strip():
            return True

        try:
            # 简单实现：解析 $.path op value
            m = re.match(r'\$\.(\w+(?:\.\w+)*)\s*(>|<|>=|<=|==|!=)\s*(.+)', condition)
            if m:
                path = m.group(1).split(".")
                op = m.group(2)
                val_str = m.group(3).strip()

                # 取值
                value = payload
                for key in path:
                    if isinstance(value, dict):
                        value = value.get(key, 0)
                    else:
                        value = 0
                        break

                # 处理 .length 后缀
                if path[-1] == "length" and isinstance(value, (list, str, dict)):
                    value = len(value)

                # 解析比较值
                if val_str.startswith('"') and val_str.endswith('"'):
                    cmp_val = val_str[1:-1]
                elif val_str.isdigit():
                    cmp_val = int(val_str)
                elif val_str.replace(".", "").replace("-", "").isdigit():
                    cmp_val = float(val_str)
                else:
                    cmp_val = val_str

                if op == ">":
                    return value > cmp_val
                elif op == "<":
                    return value < cmp_val
                elif op == ">=":
                    return value >= cmp_val
                elif op == "<=":
                    return value <= cmp_val
                elif op == "==":
                    return value == cmp_val
                elif op == "!=":
                    return value != cmp_val

            return True  # 无法解析 → 放行
        except Exception:
            return True

    async def _resolve_recipients(self, target: str, payload: dict) -> list[str]:
        """解析接收人列表。

        target 语法:
          "owner"        → 事件关联对话的创建者
          "role:工程经理"  → 拥有该角色的所有员工
          "user:EMP001"  → 指定用户
        """
        if target.startswith("user:"):
            return [target[5:]]

        if target.startswith("role:"):
            role_name = target[5:]
            return await self._query_employees_by_role(role_name)

        if target == "owner":
            owner = payload.get("conversation_owner", "") or payload.get("user_id", "")
            return [owner] if owner else []

        return []

    async def _query_employees_by_role(self, role_name: str) -> list[str]:
        """从 Neo4j 查询拥有指定角色的所有员工（含层级继承）"""
        try:
            from app.services.neo4j_service import neo4j_service

            if not neo4j_service.connected:
                return []

            namespace = settings.NEO4J_NAMESPACE or ""
            # 查询角色 → 员工，包含上级角色继承
            records = await neo4j_service.execute_read(
                """
                MATCH (r:Role {name: $role_name})
                OPTIONAL MATCH (r)-[:上级角色*0..]->(parent:Role)
                OPTIONAL MATCH (parent)-[:角色下的员工]->(e:Employee)
                WHERE e._namespace = $ns OR $ns = ''
                RETURN DISTINCT e.code AS code
                """,
                {"role_name": role_name, "ns": namespace},
            )
            return [r["code"] for r in records if r.get("code")]

        except Exception as e:
            log.warning(f"[NotificationEngine] 查询角色 {role_name} 员工失败: {e}")
            return []

    def _build_notification(self, rule: dict, payload: dict, recipient: str) -> dict:
        """用事件数据填充通知模板"""
        title = rule.get("title_template", "")
        body = rule.get("body_template", "")

        # 模板替换: {key} → payload[key]，自动翻译概念名
        for key, value in payload.items():
            if isinstance(value, list):
                display_value = ", ".join(str(v) for v in value)
            elif isinstance(value, (str, int, float)):
                display_value = str(value)
            else:
                continue
            display_value = self._translate_concept_names(display_value)
            title = title.replace(f"{{{key}}}", display_value)
            body = body.replace(f"{{{key}}}", display_value)

    def _translate_concept_names(self, text: str) -> str:
        """WorkOrderBOM → 工单BOM, BOM_compare → BOM_compare（保留 action 名）"""
        try:
            from app.services.ontology_service import ontology_service
            concepts = ontology_service.get_concepts()
            for c in concepts:
                name = c.get("name", "")
                label = c.get("label", "")
                if name and label and name in text:
                    text = text.replace(name, label)
        except Exception:
            pass
        return text

        return {
            "recipient": recipient,
            "type": rule.get("event_type", "info"),
            "severity": "warning",
            "title": title,
            "body": body,
            "source": rule.get("event_type", ""),
            "ref_conversation_id": str(payload.get("conversation_id", "")),
            "ref_chain_id": str(payload.get("chain_id", "")),
            "ref_plan_id": str(payload.get("plan_id", "")),
            "action_data": payload.get("action_data"),
            "created_at": "",
        }

    def _get_adapter(self, channel_name: str):
        """获取渠道适配器实例"""
        if channel_name == "inapp":
            from app.services.channel_adapters.inapp import InAppChannel
            return InAppChannel()
        elif channel_name in ("wecom", "wework"):
            from app.services.channel_adapters.wecom import WeComChannel
            return WeComChannel()
        elif channel_name == "email":
            from app.services.channel_adapters.email import EmailChannel
            return EmailChannel()
        elif channel_name in ("dingtalk", "ding"):
            from app.services.channel_adapters.dingtalk import DingTalkChannel
            return DingTalkChannel()
        elif channel_name == "sms":
            from app.services.channel_adapters.sms import SMSChannel
            return SMSChannel()
        elif channel_name == "webhook":
            from app.services.channel_adapters.webhook import WebhookChannel
            return WebhookChannel()
        return None


notification_engine = NotificationEngine()
