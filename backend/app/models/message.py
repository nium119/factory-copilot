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


class MessageType(str, enum.Enum):
    """消息类型枚举"""
    INFO = "info"        # 普通对话消息
    REPORT = "report"    # 查询结果报告
    CONFIRM = "confirm"  # 确认请求
    REVIEW = "review"    # 验证失败后的责任分离复核
    ALERT = "alert"      # 告警通知


class ConfirmStatus(str, enum.Enum):
    """确认状态枚举"""
    NONE = "none"          # 非确认消息
    PENDING = "pending"    # 待审批
    APPROVED = "approved"  # 已通过
    REJECTED = "rejected"  # 已拒绝
    EXPIRED = "expired"    # 已过期


class Message(Base, TimestampMixin):
    """消息数据模型"""
    __tablename__ = "agent_messages"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    conversation_id = Column(String(36), ForeignKey("agent_conversations.id", ondelete="CASCADE"), nullable=False, index=True, comment="会话ID")
    role = Column(SQLEnum(MessageRole), nullable=False, comment="消息角色")
    content = Column(Text, nullable=False, comment="消息内容")
    extra_data = Column(Text, nullable=True, comment="元数据(JSON)")
    # ── 新增: 消息类型与审批状态 ──
    message_type = Column(String(32), default=MessageType.INFO.value, nullable=False, comment="消息类型: info/report/confirm/alert")
    status = Column(String(32), default=ConfirmStatus.NONE.value, nullable=False, comment="确认状态: none/pending/approved/rejected/expired")
    assigned_to = Column(String(128), nullable=True, comment="审批目标角色名，空=不限制")
    reviewed_by = Column(String(64), nullable=True, comment="审批人 user_id")
    reviewed_at = Column(String(32), nullable=True, comment="审批时间 ISO 格式")

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
