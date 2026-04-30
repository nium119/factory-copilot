"""用户反馈模型 — 独立于消息 metadata 的反馈存储"""
import uuid

from sqlalchemy import Column, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from .base import Base, TimestampMixin


class Feedback(Base, TimestampMixin):
    __tablename__ = "feedbacks"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(64), nullable=False, index=True, comment="用户ID")
    message_id = Column(String(36), ForeignKey("messages.id", ondelete="CASCADE"), nullable=False, index=True, comment="消息ID")
    agent_name = Column(String(32), nullable=True, comment="Agent名称")
    score = Column(Integer, nullable=False, comment="评分 1-5")
    comment = Column(Text, nullable=True, comment="反馈评语")
    action = Column(String(16), nullable=True, comment="反馈动作: like/dislike/detail")

    message = relationship("Message", backref="feedbacks", passive_deletes=True)
