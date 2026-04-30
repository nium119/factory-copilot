"""用户偏好模型 — 记录用户对各 Agent 的偏好权重"""
import uuid

from sqlalchemy import Column, Float, Integer, String, Text

from .base import Base, TimestampMixin


class UserPreference(Base, TimestampMixin):
    __tablename__ = "user_preferences"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(64), nullable=False, index=True, comment="用户ID")
    agent_name = Column(String(32), nullable=False, comment="Agent名称")
    preference_weight = Column(Float, default=0.5, comment="偏好权重 (0.0~1.0)")
    interaction_count = Column(Integer, default=0, comment="交互次数")
    positive_count = Column(Integer, default=0, comment="正向反馈次数")
    negative_count = Column(Integer, default=0, comment="负向反馈次数")
    last_interaction_agent = Column(String(32), nullable=True, comment="最近使用的Agent")
    preference_tags = Column(Text, nullable=True, comment="偏好标签(JSON数组)")
