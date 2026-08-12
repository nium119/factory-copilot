"""A2A 外部 Agent 数据库模型"""
from sqlalchemy import Boolean, Column, String, Text

from app.models.base import Base, TimestampMixin


class A2aAgent(Base, TimestampMixin):
    """A2A 外部 Agent 配置表"""
    __tablename__ = "agent_a2a_agents"

    name = Column(String, primary_key=True, comment="Agent 唯一标识")
    display_name = Column(Text, nullable=False, default="", comment="显示名称")
    url = Column(Text, nullable=False, default="", comment="HTTP 端点 URL（A2A 标准）")
    enabled = Column(Boolean, default=True, nullable=False, comment="是否启用")
    description = Column(Text, nullable=False, default="", comment="描述")
    auto_collab = Column(Boolean, default=False, nullable=False, comment="是否加入自动协作池（阶段二）")
    # 注：command/args 已废弃（子进程设计改 HTTP A2A），startup 表重建时彻底移除
