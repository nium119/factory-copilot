from sqlalchemy import Column, DateTime
from sqlalchemy.sql import func
from datetime import datetime, timezone
import uuid

from sqlalchemy.orm import DeclarativeBase

class Base(DeclarativeBase):
    """SQLAlchemy基类"""
    pass

class TimestampMixin:
    """时间戳混入类"""
    # 使用本地时间
    created_at = Column(DateTime, default=lambda: datetime.now(), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(), onupdate=lambda: datetime.now(), nullable=False)
