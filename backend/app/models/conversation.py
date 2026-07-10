import json
import uuid

from sqlalchemy import Boolean, Column, Integer, String, Text
from sqlalchemy.orm import relationship

from .base import Base, TimestampMixin


class Conversation(Base, TimestampMixin):
    """会话数据模型"""
    __tablename__ = "agent_conversations"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(255), nullable=False, index=True, comment="用户ID")
    title = Column(String(255), nullable=True, comment="会话标题")
    message_count = Column(Integer, default=0, nullable=False, comment="消息数量")
    is_active = Column(Boolean, default=True, nullable=False, comment="是否激活")
    summary = Column(Text, nullable=True, comment="历史对话摘要(压缩缓存)")
    summary_version = Column(Integer, default=0, nullable=False, comment="摘要版本号")
    extra_data = Column(Text, nullable=True, comment="元数据(JSON)")

    # 关联关系
    messages = relationship("Message", back_populates="conversation", cascade="all, delete-orphan", lazy="select")

    def __repr__(self):
        return f"<Conversation(id={self.id}, user_id={self.user_id}, title={self.title})>"

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
