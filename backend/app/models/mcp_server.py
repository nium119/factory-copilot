"""MCP 服务器数据库模型"""
from sqlalchemy import Boolean, Column, String, Text

from app.models.base import Base, TimestampMixin


class McpServer(Base, TimestampMixin):
    """MCP 服务器配置表"""
    __tablename__ = "agent_mcp_servers"

    name = Column(String, primary_key=True, comment="服务器唯一标识")
    command = Column(Text, nullable=False, comment="启动命令")
    args = Column(Text, nullable=False, default="[]", comment="启动参数 JSON")
    enabled = Column(Boolean, default=True, nullable=False, comment="是否启用")
    description = Column(Text, nullable=False, default="", comment="描述")
