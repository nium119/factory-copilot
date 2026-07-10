"""A2A 外部 Agent 数据库模型"""
from sqlalchemy import Boolean, Column, String, Text

from app.models.base import Base, TimestampMixin


class A2aAgent(Base, TimestampMixin):
    """A2A 外部 Agent 配置表"""
    __tablename__ = "agent_a2a_agents"

    name = Column(String, primary_key=True, comment="Agent 唯一标识")
    display_name = Column(Text, nullable=False, default="", comment="显示名称")
    command = Column(Text, nullable=False, comment="启动命令")
    args = Column(Text, nullable=False, default="[]", comment="启动参数 JSON")
    enabled = Column(Boolean, default=True, nullable=False, comment="是否启用")
    description = Column(Text, nullable=False, default="", comment="描述")
