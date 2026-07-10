"""异常检测规则数据库模型"""
from sqlalchemy import Boolean, Column, Integer, String, Text

from app.models.base import Base, TimestampMixin


class ExplorerRule(Base, TimestampMixin):
    """异常检测规则配置表"""
    __tablename__ = "explorer_rules"

    name = Column(String, primary_key=True, comment="规则唯一标识")
    rule_type = Column(Text, nullable=False, default="threshold", comment="规则类型: threshold / graph_pattern")
    concept = Column(Text, nullable=False, default="", comment="目标概念")
    check_property = Column(Text, nullable=False, default="", comment="检测属性")
    check_op = Column(Text, nullable=False, default=">", comment="检测操作符")
    check_value = Column(Text, nullable=False, default="", comment="检测阈值")
    graph_query = Column(Text, nullable=False, default="", comment="图查询语句")
    graph_params = Column(Text, nullable=False, default="{}", comment="图查询参数 JSON")
    severity = Column(Text, nullable=False, default="medium", comment="严重程度: low / medium / high")
    title_template = Column(Text, nullable=False, default="", comment="告警标题模板")
    description_template = Column(Text, nullable=False, default="", comment="告警描述模板")
    suggestion = Column(Text, nullable=False, default="", comment="处理建议")
    enabled = Column(Boolean, default=True, nullable=False, comment="是否启用")
