"""动态 Skill 模型 — 声明式工具，运行时配置，不依赖本体推送链路"""
from sqlalchemy import Boolean, Column, String, Text

from app.models.base import Base, TimestampMixin


class DynamicSkill(Base, TimestampMixin):
    """动态 Skill 表：声明式只读工具（cypher 模板/聚合），写能力映射到已建模 action"""
    __tablename__ = "agent_skills"

    name = Column(String(128), primary_key=True, comment="技能唯一标识")
    display_name = Column(String(128), nullable=False, default="", comment="显示名")
    description = Column(Text, nullable=False, default="", comment="描述")
    type = Column(String(32), nullable=False, default="concept_query", comment="concept_query|aggregate|transform")
    concept = Column(String(64), nullable=False, default="", comment="关联概念（可选）")
    param_schema = Column(Text, nullable=False, default="[]", comment="参数 schema JSON")
    implementation = Column(Text, nullable=False, default="{}", comment="执行实现 JSON {kind, template, action_name}")
    risk = Column(String(16), nullable=False, default="READ", comment="READ|WRITE_AUDIT")
    enabled = Column(Boolean, default=True, nullable=False, comment="是否启用")
