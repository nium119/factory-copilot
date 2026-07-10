import enum
import json
import uuid

from sqlalchemy import Column, ForeignKey, String, Text
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import relationship

from .base import Base, TimestampMixin


class MessageRole(str, enum.Enum):
    """消息角色枚举"""
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"

class Message(Base, TimestampMixin):
    """消息数据模型"""
    __tablename__ = "agent_messages"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    conversation_id = Column(String(36), ForeignKey("agent_conversations.id", ondelete="CASCADE"), nullable=False, index=True, comment="会话ID")
    role = Column(SQLEnum(MessageRole), nullable=False, comment="消息角色")
    content = Column(Text, nullable=False, comment="消息内容")
    extra_data = Column(Text, nullable=True, comment="元数据(JSON)")

    # 关联关系
    conversation = relationship("Conversation", back_populates="messages")

    def __repr__(self):
        return f"<Message(id={self.id}, conversation_id={self.conversation_id}, role={self.role})>"

    @property
    def metadata_dict(self) -> dict:
        """获取元数据字典"""
        if self.extra_data:
            try:
                return json.loads(self.extra_data)
            except (json.JSONDecodeError, TypeError):
                return {}
        return {}

    @metadata_dict.setter
    def metadata_dict(self, value: dict):
        """设置元数据字典"""
        self.extra_data = json.dumps(value) if value else None
