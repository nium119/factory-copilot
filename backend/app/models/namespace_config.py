"""命名空间配置模型"""
from sqlalchemy import Column, String, Text
from app.models.base import Base

class NamespaceConfig(Base):
    __tablename__ = "namespace_configs"

    namespace = Column(String(64), primary_key=True)
    config_type = Column(String(64), primary_key=True)
    config_data = Column(Text, default="{}")
    updated_at = Column(String(32), default="")
