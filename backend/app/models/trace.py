"""LLM 调用追踪模型 — 一次对话一条 trace，spans 存 JSON"""
from sqlalchemy import Column, Integer, String, Text

from app.models.base import Base, TimestampMixin


class AgentTrace(Base, TimestampMixin):
    """对话追踪表（agent_traces）

    每次对话一条 trace，记录端到端耗时、LLM 调用次数、token 用量，
    spans 列存全链路 span 数组（JSON），供线上排障回放。
    """

    __tablename__ = "agent_traces"

    id = Column(Integer, primary_key=True, autoincrement=True)
    trace_id = Column(String(32), nullable=False, index=True, comment="本次对话唯一追踪 ID")
    namespace = Column(String(64), default="", comment="本体图谱项目（namespace）")
    user_id = Column(String(64), default="")
    conversation_id = Column(String(64), default="")
    message = Column(String(200), default="", comment="用户原始输入（截断）")
    status = Column(String(16), default="ok", comment="ok / error")
    total_ms = Column(Integer, default=0, comment="端到端总耗时（毫秒）")
    llm_calls = Column(Integer, default=0, comment="LLM 调用次数")
    total_tokens = Column(Integer, default=0, comment="token 合计（读不到真实值回退估算并打标）")
    spans = Column(Text, default="[]", comment="span 数组 JSON")
    error = Column(String(500), default="")
