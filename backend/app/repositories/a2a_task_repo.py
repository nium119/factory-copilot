"""A2A 任务持久化 Repository — agent_a2a_tasks 表"""
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.a2a_task import A2aTask


class A2aTaskRepository:
    """A2A 任务持久化 Repository（upsert + 查询 + 删除）"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def save(self, *, task_id: str, context_id: str, namespace: str,
                   status: str, payload: str, error: str = "") -> A2aTask:
        """按 task_id upsert 任务快照"""
        obj = await self.db.get(A2aTask, task_id)
        if obj is None:
            obj = A2aTask(
                task_id=task_id, context_id=context_id, namespace=namespace,
                status=status, payload=payload, error=error,
            )
            self.db.add(obj)
        else:
            obj.context_id = context_id
            obj.namespace = namespace
            obj.status = status
            obj.payload = payload
            obj.error = error
        await self.db.commit()
        return obj

    async def get(self, task_id: str) -> Optional[A2aTask]:
        """按 task_id 查询"""
        return await self.db.get(A2aTask, task_id)

    async def delete(self, task_id: str) -> bool:
        """删除任务（清理已完成任务）"""
        obj = await self.db.get(A2aTask, task_id)
        if obj is None:
            return False
        await self.db.delete(obj)
        await self.db.commit()
        return True
