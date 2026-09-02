"""告警 Repository — 去重 + 状态管理"""
import json
from datetime import datetime
from typing import Any, List, Optional

from sqlalchemy import and_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alert import Alert


class AlertRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def exists(self, rule_name: str, entity_id: str) -> bool:
        """检查同一规则+实体是否已有未处理的告警（去重）。"""
        result = await self.db.execute(
            select(Alert.id).where(
                and_(
                    Alert.rule_name == rule_name,
                    Alert.entity_id == entity_id,
                    Alert.status.in_(["detected", "escalated"]),
                )
            ).limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def create(self, alert_data: dict) -> Alert:
        alert = Alert(
            rule_name=alert_data["rule_name"],
            rule_label=alert_data.get("rule_label", ""),
            concept_name=alert_data.get("concept_name", ""),
            entity_id=alert_data.get("entity_id", ""),
            severity=alert_data.get("severity", "warning"),
            status="detected",
            agents=json.dumps(alert_data.get("agents", [])),
            trigger_condition=alert_data.get("trigger_condition", ""),
            description=alert_data.get("description", ""),
        )
        self.db.add(alert)
        await self.db.commit()
        await self.db.refresh(alert)
        return alert

    async def acknowledge(self, alert_id: str) -> Optional[Alert]:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        await self.db.execute(
            update(Alert)
            .where(Alert.id == alert_id)
            .values(status="acknowledged", acknowledged_at=now)
        )
        await self.db.commit()
        result = await self.db.execute(select(Alert).where(Alert.id == alert_id))
        return result.scalar_one_or_none()

    async def resolve(self, alert_id: str) -> Optional[Alert]:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        await self.db.execute(
            update(Alert)
            .where(Alert.id == alert_id)
            .values(status="resolved", resolved_at=now)
        )
        await self.db.commit()
        result = await self.db.execute(select(Alert).where(Alert.id == alert_id))
        return result.scalar_one_or_none()

    async def list_active(
        self, limit: int = 50, agent_name: Optional[str] = None,
    ) -> List[Alert]:
        conditions = [Alert.status.in_(["detected", "escalated"])]
        result = await self.db.execute(
            select(Alert)
            .where(and_(*conditions))
            .order_by(Alert.created_at.desc())
            .limit(limit)
        )
        alerts = list(result.scalars().all())

        if agent_name:
            alerts = [
                a for a in alerts
                if agent_name in (json.loads(a.agents or "[]"))
            ]
        return alerts

    async def escalate_stale(self, hours: int = 24) -> int:
        """超时未确认的告警自动升级。"""
        from sqlalchemy import text
        result = await self.db.execute(
            text("""
                UPDATE agent_alerts SET status = 'escalated'
                WHERE status = 'detected'
                AND created_at < datetime('now', '-' || :hours || ' hours')
            """),
            {"hours": str(hours)},
        )
        await self.db.commit()
        return result.rowcount
