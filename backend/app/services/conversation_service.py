"""
会话管理服务
处理会话的核心业务逻辑
"""
from typing import List, Optional, Tuple
from loguru import logger

from app.repositories.conversation_repository import ConversationRepository
from app.repositories.message_repository import MessageRepository
from app.services.vector_memory_service import vector_memory_service
from app.models.conversation import Conversation
from app.models.message import Message, MessageRole


class ConversationService:
    """会话管理服务"""

    def __init__(
        self,
        conversation_repo: ConversationRepository,
        message_repo: MessageRepository
    ):
        self.conversation_repo = conversation_repo
        self.message_repo = message_repo

    async def create(
        self,
        user_id: str,
        title: Optional[str] = None,
        metadata: Optional[dict] = None
    ) -> Conversation:
        """
        创建新会话

        Args:
            user_id: 用户ID
            title: 会话标题
            metadata: 元数据

        Returns:
            会话对象
        """
        conversation = await self.conversation_repo.create(
            user_id=user_id,
            title=title,
            metadata=metadata
        )
        logger.info(f"Created conversation {conversation.id} for user {user_id}")
        return conversation

    async def get_list(
        self,
        user_id: str,
        page: int = 1,
        page_size: int = 20,
        search: Optional[str] = None,
        is_active: Optional[bool] = None
    ) -> Tuple[List[Conversation], int]:
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
        return await self.conversation_repo.get_by_user(
            user_id=user_id,
            page=page,
            page_size=page_size,
            search=search,
            is_active=is_active
        )

    async def get_by_id(self, conversation_id: str) -> Optional[Conversation]:
        """根据ID获取会话"""
        return await self.conversation_repo.get_by_id(conversation_id)

    async def update(
        self,
        conversation_id: str,
        title: Optional[str] = None,
        is_active: Optional[bool] = None,
        metadata: Optional[dict] = None
    ) -> Optional[Conversation]:
        """更新会话"""
        conversation = await self.conversation_repo.update(
            conversation_id=conversation_id,
            title=title,
            is_active=is_active,
            metadata=metadata
        )
        if conversation:
            logger.info(f"Updated conversation {conversation_id}")
        return conversation

    async def update_title(
        self,
        conversation_id: str,
        title: str
    ) -> Optional[Conversation]:
        """
        更新会话标题

        Args:
            conversation_id: 会话ID
            title: 新标题

        Returns:
            更新后的会话对象
        """
        conversation = await self.conversation_repo.update(
            conversation_id=conversation_id,
            title=title
        )
        if conversation:
            logger.info(f"Updated title for conversation {conversation_id}")
        return conversation

    async def delete(self, conversation_id: str) -> bool:
        """
        删除会话(级联删除消息和向量)

        Args:
            conversation_id: 会话ID

        Returns:
            是否成功
        """
        # 删除向量记忆
        await vector_memory_service.delete_by_conversation(conversation_id)

        # 删除消息(数据库级联删除会自动处理)
        deleted = await self.conversation_repo.delete(conversation_id)

        if deleted:
            logger.info(f"Deleted conversation {conversation_id}")
        return deleted

    async def auto_generate_title(self, conversation_id: str) -> Optional[str]:
        """
        自动生成会话标题

        Args:
            conversation_id: 会话ID

        Returns:
            生成的标题
        """
        # 获取第一条用户消息
        messages = await self.message_repo.get_by_conversation(
            conversation_id=conversation_id,
            limit=10
        )

        user_message = None
        for msg in messages:
            if msg.role == MessageRole.USER:
                user_message = msg
                break

        if not user_message:
            return None

        # 取前20个字符作为标题
        title = user_message.content[:20]
        if len(user_message.content) > 20:
            title += "..."

        # 更新标题
        await self.update_title(conversation_id, title)
        return title

    async def get_messages(
        self,
        conversation_id: str,
        limit: Optional[int] = None,
        offset: int = 0
    ) -> List[Message]:
        """
        获取会话的消息列表

        Args:
            conversation_id: 会话ID
            limit: 限制数量
            offset: 偏移量

        Returns:
            消息列表
        """
        return await self.message_repo.get_by_conversation(
            conversation_id=conversation_id,
            limit=limit,
            offset=offset
        )

    async def increment_message_count(self, conversation_id: str) -> bool:
        """增加消息计数"""
        return await self.conversation_repo.increment_message_count(conversation_id)
