"""API 调用日志模型"""
from sqlalchemy import Column, Integer, String, Text
from app.models.base import Base

class ApiCallLog(Base):
    __tablename__ = "api_call_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(String(32), default="")
    user_id = Column(String(64), default="")
    conversation_id = Column(String(64), default="")
    message = Column(String(200), default="")
    concept = Column(String(64), default="")
    method = Column(String(10), default="")
    url = Column(String(500), default="")
    status = Column(Integer, default=0)
    elapsed_ms = Column(Integer, default=0)
    error = Column(String(500), default="")
    request_body = Column(Text, default="")
    response_body = Column(Text, default="")
    context = Column(Text, default="")
