"""通知体系 — 通知规则、通知实体、用户订阅"""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text

from .base import Base, TimestampMixin


class NotificationRule(Base, TimestampMixin):
    """通知规则 — 匹配事件并决定通知谁、通过什么渠道"""

    __tablename__ = "agent_notification_rules"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_type = Column(String(64), nullable=False, index=True, comment="事件类型")
    condition = Column(String(255), nullable=True, comment="JSONPath 条件，如 $.missing_actions_count > 0")
    target = Column(String(128), nullable=False, comment="接收人: owner | role:工程经理 | user:EMP001")
    channels = Column(String(255), nullable=False, default='["inapp"]', comment="渠道 JSON 数组")
    title_template = Column(String(255), nullable=False, comment="标题模板")
    body_template = Column(String(512), nullable=False, comment="正文模板")
    enabled = Column(Boolean, default=True, comment="是否启用")
    priority = Column(Integer, default=0, comment="排序优先级，越大越优先")

    def __repr__(self):
        return f"<NotificationRule(id={self.id}, event_type={self.event_type}, target={self.target})>"


class Notification(Base, TimestampMixin):
    """通知实体 — 发送给用户的每一条通知"""

    __tablename__ = "agent_notifications"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    recipient = Column(String(64), nullable=False, index=True, comment="接收人工号")
    type = Column(String(32), nullable=False, comment="通知类型: action_missing|execution_failed|system_alert|action_ready")
    severity = Column(String(16), default="info", comment="严重程度: info|warning|critical")
    title = Column(String(255), nullable=False, comment="通知标题")
    body = Column(Text, nullable=False, comment="通知正文")
    channel = Column(String(16), default="inapp", comment="发送渠道: inapp|wecom")
    status = Column(String(16), default="unread", index=True, comment="状态: unread|read|archived")
    source = Column(String(64), nullable=True, comment="触发源: plan.generated|plan.executed|schema.pushed|system.alert")
    ref_conversation_id = Column(String(36), nullable=True, comment="关联会话 ID")
    ref_chain_id = Column(String(128), nullable=True, comment="关联链 ID")
    ref_plan_id = Column(String(64), nullable=True, comment="关联方案 ID")
    action_data = Column(Text, nullable=True, comment="JSON: 携带操作数据（如 missing_actions 列表）")
    read_at = Column(DateTime, nullable=True, comment="阅读时间")

    def __repr__(self):
        return f"<Notification(id={self.id}, recipient={self.recipient}, type={self.type}, status={self.status})>"


class UserSubscription(Base, TimestampMixin):
    """用户订阅偏好 — 决定用户是否接收某种类型的通知"""

    __tablename__ = "agent_user_subscriptions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(64), nullable=False, index=True, comment="用户工号")
    event_type = Column(String(64), nullable=False, comment="事件类型")
    channel = Column(String(16), default="inapp", comment="通知渠道")
    enabled = Column(Boolean, default=True, comment="是否启用")

    def __repr__(self):
        return f"<UserSubscription(user_id={self.user_id}, event_type={self.event_type}, enabled={self.enabled})>"
