"""事件队列 — 事件持久化，支持重试"""
from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, Text

from .base import Base, TimestampMixin


class EventQueue(Base, TimestampMixin):
    """事件队列表 — 生产者 emit 写入，EventDispatcherWorker 消费"""

    __tablename__ = "agent_event_queue"

    id = Column(Integer, primary_key=True, autoincrement=True)
    type = Column(String(64), nullable=False, index=True, comment="事件类型: plan.generated | plan.executed | schema.pushed | approval.required | system.alert")
    payload = Column(Text, nullable=False, comment="JSON 事件数据")
    status = Column(String(16), default="pending", index=True, comment="pending | processing | processed | failed")
    retry_count = Column(Integer, default=0, comment="已重试次数")
    max_retries = Column(Integer, default=3, comment="最大重试次数")
    processed_at = Column(DateTime, nullable=True, comment="处理完成时间")
    error_message = Column(Text, nullable=True, comment="失败原因")

    def __repr__(self):
        return f"<EventQueue(id={self.id}, type={self.type}, status={self.status})>"
