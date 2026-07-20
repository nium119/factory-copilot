"""意图反馈模型 — 记录用户对路由结果的纠正"""
from datetime import datetime
from sqlalchemy import Column, String, Text, DateTime, Integer, Float
from .base import Base


class IntentFeedback(Base):
    __tablename__ = "intent_feedbacks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    message = Column(String(500), nullable=False, comment="用户原始消息")
    matched_action = Column(String(128), nullable=False, comment="匹配到的操作名")
    correct_action = Column(String(128), nullable=True, comment="用户期望的正确操作(可选)")
    was_correct = Column(Integer, default=0, comment="0=错误匹配 1=正确匹配")
    user_id = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "message": self.message,
            "matched_action": self.matched_action,
            "correct_action": self.correct_action,
            "was_correct": self.was_correct,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
