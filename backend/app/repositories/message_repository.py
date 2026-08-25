"""
消息Repository
处理消息数据的CRUD操作
"""
from typing import List, Optional

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.message import Message, MessageRole, MessageType, ConfirmStatus


class MessageRepository:
    """消息Repository"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(
        self,
        conversation_id: str,
        role: MessageRole,
        content: str,
        metadata: Optional[dict] = None,
        message_type: str = MessageType.INFO.value,
        status: str = ConfirmStatus.NONE.value,
        assigned_to: Optional[str] = None,
    ) -> Message:
        """创建消息"""
        message = Message(
            conversation_id=conversation_id,
            role=role,
            content=content,
            metadata_dict=metadata or {},
            message_type=message_type,
            status=status,
            assigned_to=assigned_to,
        )
        self.db.add(message)
        await self.db.commit()
        await self.db.refresh(message)
        return message

    async def get_pending_confirmations(
        self,
        assigned_to: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list:
        """查询待审批的确认消息。assigned_to 为空时返回所有待审批消息。"""
        query = select(Message).where(
            Message.message_type.in_([MessageType.CONFIRM.value, MessageType.REVIEW.value]),
            Message.status == ConfirmStatus.PENDING.value,
        ).order_by(Message.created_at.desc())
        if assigned_to:
            query = query.where(Message.assigned_to == assigned_to)
        query = query.offset(offset).limit(limit)
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def count_pending_confirmations(
        self, assigned_to: Optional[str] = None,
    ) -> int:
        """统计待审批的确认消息总数。"""
        query = select(func.count()).where(
            Message.message_type.in_([MessageType.CONFIRM.value, MessageType.REVIEW.value]),
            Message.status == ConfirmStatus.PENDING.value,
        )
        if assigned_to:
            query = query.where(Message.assigned_to == assigned_to)
        result = await self.db.execute(query)
        return result.scalar() or 0

    async def get_processed_confirmations(
        self, limit: int = 50, offset: int = 0,
    ) -> list:
        """查询已处理（通过/拒绝）的确认消息。

        按提交时间（created_at）倒序，与待审批列表（get_pending_confirmations）
        保持一致——让最新提交的审批排在最前面，避免按处理时间（updated_at）排序
        导致最晚提交的条目落到列表底部。
        """
        query = select(Message).where(
            Message.message_type.in_([MessageType.CONFIRM.value, MessageType.REVIEW.value]),
            Message.status.in_([ConfirmStatus.APPROVED.value, ConfirmStatus.REJECTED.value]),
        ).order_by(Message.created_at.desc()).offset(offset).limit(limit)
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def count_processed_confirmations(self) -> int:
        """统计已处理的确认消息总数。"""
        query = select(func.count()).where(
            Message.message_type.in_([MessageType.CONFIRM.value, MessageType.REVIEW.value]),
            Message.status.in_([ConfirmStatus.APPROVED.value, ConfirmStatus.REJECTED.value]),
        )
        result = await self.db.execute(query)
        return result.scalar() or 0

    async def bulk_delete(self, message_ids: list[str]) -> int:
        """批量删除消息。返回删除数量。"""
        if not message_ids:
            return 0
        result = await self.db.execute(
            sa_delete(Message).where(Message.id.in_(message_ids))
        )
        await self.db.commit()
        return result.rowcount

    async def resolve_confirmation(
        self,
        message_id: str,
        approved: bool,
        reviewed_by: str = "",
    ) -> Optional[Message]:
        """审批确认消息。"""
        message = await self.get_by_id(message_id)
        if not message:
            return None
        from datetime import datetime
        message.status = ConfirmStatus.APPROVED.value if approved else ConfirmStatus.REJECTED.value
        message.reviewed_by = reviewed_by or ""
        message.reviewed_at = datetime.now().isoformat()
        await self.db.commit()
        await self.db.refresh(message)
        return message

    async def get_by_conversation(
        self,
        conversation_id: str,
        limit: Optional[int] = None,
        offset: int = 0,
        latest: bool = False
    ) -> List[Message]:
        """
        获取会话的消息列表

        Args:
            conversation_id: 会话ID
            limit: 限制数量
            offset: 偏移量
            latest: True 时倒序取最近 limit 条（再转时间升序返回），供"当前上下文"抽屉使用

        Returns:
            消息列表
        """
        query = select(Message).where(
            Message.conversation_id == conversation_id
        )

        if latest:
            # 取最近 N 条（时间倒序），返回前转升序，保证上下文按时间正序展示
            q = query.order_by(Message.created_at.desc())
            if limit:
                q = q.limit(limit)
            result = await self.db.execute(q)
            rows = list(result.scalars().all())
            rows.reverse()
            return rows

        q = query.order_by(Message.created_at.asc())
        if limit:
            q = q.offset(offset).limit(limit)
        result = await self.db.execute(q)
        return list(result.scalars().all())

    async def get_around_message(
        self,
        conversation_id: str,
        message_id: str,
        before: int = 10,
        after: int = 10
    ) -> Optional[List[Message]]:
        """定位锚点消息附近的上下文（前 before 条 + 锚点 + 后 after 条，时间正序）。

        供"打开原对话"抽屉按变更方案消息定位展示当前上下文，而非取整个历史。
        """
        anchor = await self.get_by_id(message_id)
        if not anchor or anchor.conversation_id != conversation_id:
            return None
        # 锚点之前 before 条（倒序取再反转）
        q_before = select(Message).where(
            Message.conversation_id == conversation_id,
            Message.created_at < anchor.created_at
        ).order_by(Message.created_at.desc()).limit(before)
        rows_before = list((await self.db.execute(q_before)).scalars().all())
        rows_before.reverse()
        # 锚点之后 after 条
        q_after = select(Message).where(
            Message.conversation_id == conversation_id,
            Message.created_at > anchor.created_at
        ).order_by(Message.created_at.asc()).limit(after)
        rows_after = list((await self.db.execute(q_after)).scalars().all())
        return rows_before + [anchor] + rows_after

    async def get_last_message(self, conversation_id: str) -> Optional[Message]:
        """获取会话的最后一条消息"""
        query = select(Message).where(
            Message.conversation_id == conversation_id
        ).order_by(Message.created_at.desc()).limit(1)

        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    async def delete_by_conversation(self, conversation_id: str) -> int:
        """
        删除会话的所有消息

        Args:
            conversation_id: 会话ID

        Returns:
            删除的消息数量
        """
        # 先统计数量
        count = await self.count_by_conversation(conversation_id)

        # 批量删除消息
        await self.db.execute(
            sa_delete(Message).where(Message.conversation_id == conversation_id)
        )
        await self.db.commit()
        return count

    async def count_by_conversation(self, conversation_id: str) -> int:
        """统计会话的消息数量"""
        query = select(func.count()).where(
            Message.conversation_id == conversation_id
        )
        result = await self.db.execute(query)
        return result.scalar() or 0

    async def get_by_id(self, message_id: str) -> Optional[Message]:
        """根据ID获取消息"""
        result = await self.db.execute(
            select(Message).where(Message.id == message_id)
        )
        return result.scalar_one_or_none()

    async def get_latest_with_metadata(self, limit: int = 50, offset: int = 0) -> list:
        """获取最近的有 extra_data 的消息（用于提示词日志）。"""
        query = (
            select(Message)
            .where(Message.extra_data.isnot(None), Message.extra_data != "")
            .order_by(Message.created_at.desc())
            .offset(offset).limit(limit)
        )
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def update_metadata(self, message_id: str, metadata: dict) -> bool:
        """更新消息的 metadata"""
        message = await self.get_by_id(message_id)
        if not message:
            return False
        message.metadata_dict = metadata
        await self.db.commit()
        return True
