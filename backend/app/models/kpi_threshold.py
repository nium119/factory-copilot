"""KPI 阈值数据库模型"""
from sqlalchemy import Boolean, Column, Float, String, Text

from app.models.base import Base, TimestampMixin


class KpiThreshold(Base, TimestampMixin):
    """KPI 指标阈值配置表"""
    __tablename__ = "agent_kpi_thresholds"

    kpi_key = Column(String, primary_key=True, comment="KPI 唯一标识")
    name = Column(Text, nullable=False, default="", comment="指标名称")
    target = Column(Float, nullable=False, default=0, comment="目标值")
    unit = Column(Text, nullable=False, default="", comment="单位")
    direction = Column(Text, nullable=False, default="higher_better", comment="方向: higher_better / lower_better")
    warning_threshold = Column(Float, nullable=False, default=0, comment="告警阈值")
    critical_threshold = Column(Float, nullable=False, default=0, comment="严重阈值")
    domain = Column(Text, nullable=False, default="", comment="所属域")
    enabled = Column(Boolean, default=True, nullable=False, comment="是否启用")
