"""告警模型 — 触发器命中的持久化记录"""
import uuid

from sqlalchemy import Column, String, Text

from .base import Base, TimestampMixin


class Alert(Base, TimestampMixin):
    __tablename__ = "agent_alerts"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    rule_name = Column(String(64), nullable=False, index=True, comment="规则名")
    rule_label = Column(String(128), nullable=True, comment="规则中文标签")
    concept_name = Column(String(64), nullable=False, index=True, comment="触发概念")
    entity_id = Column(String(128), nullable=False, index=True, comment="触发的实体ID")
    severity = Column(String(16), nullable=False, default="warning", comment="严重级别")
    status = Column(
        String(16), nullable=False, default="detected", index=True,
        comment="状态: detected|acknowledged|resolved|escalated",
    )
    agents = Column(Text, nullable=True, comment="关联Agent列表(JSON数组)")
    trigger_condition = Column(Text, nullable=True, comment="触发条件表达式")
    description = Column(Text, nullable=True, comment="告警描述")
    acknowledged_at = Column(String(32), nullable=True, comment="确认时间")
    resolved_at = Column(String(32), nullable=True, comment="解决时间")
