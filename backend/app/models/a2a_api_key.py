"""A2A 服务端 API Key 数据库模型 — 外部系统调用 FC 的鉴权凭证"""
from sqlalchemy import Boolean, Column, DateTime, String, Text

from app.models.base import Base, TimestampMixin


class A2aApiKey(Base, TimestampMixin):
    """A2A 服务端 API Key 表

    FC 作为 A2A 服务端（被外部系统调用）时，外部系统携带此 Key 鉴权。
    - key_hash 存 SHA256（验签比对），key_plain 存明文（能力开放列表展示用）
    - scopes 为业务域白名单（JSON 数组，如 ["agent_production"]），空 = 无权限
    - last_used_at 验签时更新，用于可追溯
    """
    __tablename__ = "agent_a2a_api_keys"

    name = Column(String, primary_key=True, comment="Key 备注名（如「MES 调用」）")
    key_hash = Column(String(64), nullable=False, comment="SHA256(key) hex，验签比对")
    key_prefix = Column(String(16), nullable=False, default="", comment="key 前缀（脱敏显示）")
    key_plain = Column(String(128), nullable=False, default="", comment="明文 key（能力开放列表展示用）")
    scopes = Column(Text, nullable=False, default="[]", comment="业务域白名单 JSON 数组，空=无权限")
    enabled = Column(Boolean, default=True, nullable=False, comment="是否启用")
    last_used_at = Column(DateTime, nullable=True, comment="最近使用时间（验签时更新）")
