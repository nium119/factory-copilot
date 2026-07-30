"""系统配置 — 前端可编辑，存 DB，无需重启（Neo4j 连接等需重启后生效）"""
from sqlalchemy import Column, String, Text

from .base import Base, TimestampMixin


class SystemConfig(Base, TimestampMixin):
    """系统级配置 — key-value 存储，优先级高于 .env"""

    __tablename__ = "agent_system_configs"

    key = Column(String(64), primary_key=True, comment="配置键: neo4j_uri | mes_api_url | ...")
    value = Column(Text, nullable=False, default="", comment="配置值")
    description = Column(String(255), nullable=True, comment="说明")
