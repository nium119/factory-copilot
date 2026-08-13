"""A2A 任务持久化模型 — agent_a2a_tasks 表

FC 作为 A2A 服务端被外部系统调用时，任务状态持久化到本表，
重启后仍可查询 / 取消（替代原内存字典 _tasks）。
"""
from sqlalchemy import Column, String, Text

from app.models.base import Base, TimestampMixin


class A2aTask(Base, TimestampMixin):
    """A2A 任务表（agent_a2a_tasks）

    - task_id 为 A2A Task id（uuid），主键
    - payload 存 Task 完整 JSON（protocol.Task.model_dump），status 列冗余便于过滤
    - error 记录失败原因（截断 500）
    """

    __tablename__ = "agent_a2a_tasks"

    task_id = Column(String(36), primary_key=True, comment="A2A Task id（uuid）")
    context_id = Column(String(64), nullable=False, default="", index=True, comment="contextId（= conversation_id）")
    namespace = Column(String(64), nullable=False, default="", comment="任务创建时的活跃 namespace")
    status = Column(String(16), nullable=False, default="submitted", index=True, comment="submitted/working/completed/failed/canceled")
    payload = Column(Text, nullable=False, default="{}", comment="Task 完整 JSON（model_dump）")
    error = Column(String(500), nullable=False, default="", comment="失败原因（截断）")
