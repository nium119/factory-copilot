"""渠道配置 — 前端可编辑，存 DB，无需重启"""
from sqlalchemy import Column, String, Text

from .base import Base, TimestampMixin


class ChannelConfig(Base, TimestampMixin):
    """通知渠道配置 — key-value 存储"""

    __tablename__ = "agent_channel_configs"

    key = Column(String(64), primary_key=True, comment="配置键: wecom_webhook | smtp_host | ...")
    value = Column(Text, nullable=False, default="", comment="配置值")
    description = Column(String(255), nullable=True, comment="说明")
