"""
会话Repository
处理会话数据的CRUD操作
"""
from typing import List, Optional
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime

from app.models.conversation import Conversation
from app.models.message import Message


class ConversationRepository:
    """会话Repository"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(
        self,
        user_id: str,
        title: Optional[str] = None,
        metadata: Optional[dict] = None
    ) -> Conversation:
        """创建会话"""
        conversation = Conversation(
            user_id=user_id,
            title=title,
            metadata_dict=metadata or {}
        )
        self.db.add(conversation)
        await self.db.commit()
        await self.db.refresh(conversation)
        return conversation

    async def get_by_id(self, conversation_id: str) -> Optional[Conversation]:
        """根据ID获取会话"""
        result = await self.db.execute(
            select(Conversation).where(Conversation.id == conversation_id)
        )
        return result.scalar_one_or_none()

    async def get_by_user(
        self,
        user_id: str,
        page: int = 1,
        page_size: int = 20,
        search: Optional[str] = None,
        is_active: Optional[bool] = None
    ) -> tuple[List[Conversation], int]:
        """
        获取用户的会话列表

        Args:
            user_id: 用户ID
            page: 页码
            page_size: 每页大小
            search: 搜索关键词
            is_active: 是否激活

        Returns:
            (会话列表, 总数)
        """
        # 构建查询
        query = select(Conversation).where(Conversation.user_id == user_id)

        # 过滤条件
        if is_active is not None:
            query = query.where(Conversation.is_active == is_active)

        if search:
            query = query.where(Conversation.title.ilike(f"%{search}%"))

        # 排序
        query = query.order_by(Conversation.updated_at.desc())

        # 计算总数
        count_query = select(func.count()).select_from(query.subquery())
        total_result = await self.db.execute(count_query)
        total = total_result.scalar()

        # 分页
        offset = (page - 1) * page_size
        query = query.offset(offset).limit(page_size)

        # 执行查询
        result = await self.db.execute(query)
        conversations = result.scalars().all()

        return list(conversations), total

    async def update(
        self,
        conversation_id: str,
        title: Optional[str] = None,
        is_active: Optional[bool] = None,
        metadata: Optional[dict] = None
    ) -> Optional[Conversation]:
        """更新会话"""
        conversation = await self.get_by_id(conversation_id)
        if not conversation:
            return None

        if title is not None:
            conversation.title = title
        if is_active is not None:
            conversation.is_active = is_active
        if metadata is not None:
            conversation.metadata_dict = metadata

        conversation.updated_at = datetime.now()
        await self.db.commit()
        await self.db.refresh(conversation)
        return conversation

    async def delete(self, conversation_id: str) -> bool:
        """删除会话（全部用 raw SQL 避免 ORM relationship 级联问题）"""
        from app.models.feedback import Feedback
        from sqlalchemy import delete as sa_delete, text

        conversation = await self.get_by_id(conversation_id)
        if not conversation:
            return False

        # 全部用 bulk DELETE（纯 SQL，不触发 ORM relationship 处理）
        fb_subquery = select(Message.id).where(Message.conversation_id == conversation_id).scalar_subquery()
        await self.db.execute(sa_delete(Feedback).where(Feedback.message_id.in_(fb_subquery)))
        await self.db.execute(sa_delete(Message).where(Message.conversation_id == conversation_id))
        await self.db.execute(sa_delete(Conversation).where(Conversation.id == conversation_id))
        await self.db.commit()
        return True

    async def increment_message_count(self, conversation_id: str) -> bool:
        """增加消息计数"""
        conversation = await self.get_by_id(conversation_id)
        if not conversation:
            return False

        conversation.message_count += 1
        conversation.updated_at = datetime.now()
        await self.db.commit()
        return True

    async def update_summary(self, conversation_id: str, summary: str) -> bool:
        """更新会话摘要"""
        conversation = await self.get_by_id(conversation_id)
        if not conversation:
            return False

        conversation.summary = summary
        conversation.summary_version += 1
        conversation.updated_at = datetime.now()
        await self.db.commit()
        return True
