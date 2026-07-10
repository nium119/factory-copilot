"""Agent 数据库模型"""
from sqlalchemy import JSON, Boolean, Column, Integer, String, Text

from app.models.base import Base, TimestampMixin


class Agent(Base, TimestampMixin):
    """智能体配置表"""
    __tablename__ = "agent_agents"

    name = Column(String(50), primary_key=True, comment="Agent 唯一标识")
    display_name = Column(String(100), nullable=False, comment="显示名称")
    icon = Column(String(10), nullable=False, default="🤖", comment="图标 emoji")
    color = Column(String(20), default="#6c5ce7", comment="主题色")
    description = Column(String(300), default="", comment="功能描述")
    enabled = Column(Boolean, default=True, nullable=False, comment="是否启用")
    roles = Column(JSON, default=list, comment="可用角色列表，空表示所有用户可见")
    keywords = Column(JSON, default=list, comment="意图匹配关键词列表")
    system_prompt = Column(Text, nullable=True, comment="自定义系统提示词")
    project_description = Column(Text, default="", comment="本体项目行业描述")
    sort_order = Column(Integer, default=0, comment="排序权重，越大越靠前")
