"""
消息处理服务
集成长期记忆检索和上下文注入，从数据库加载历史消息作为LLM上下文
采用混合记忆策略：保留最近 N 条完整消息 + 旧消息摘要压缩
"""
from typing import AsyncGenerator, Optional, List, Tuple
from loguru import logger
import asyncio

from langchain_core.messages import HumanMessage as LCHumanMessage, AIMessage as LCAIMessage, SystemMessage as LCSystemMessage

from app.services.llm_service import llm_service
from app.services.vector_memory_service import vector_memory_service
from app.repositories.message_repository import MessageRepository
from app.repositories.conversation_repository import ConversationRepository
from app.models.message import Message, MessageRole
from app.models.conversation import Conversation
from app.core.config import settings


class MessageService:
    """消息处理服务"""

    def __init__(
        self,
        message_repo: MessageRepository,
        conversation_repo: ConversationRepository
    ):
        self.message_repo = message_repo
        self.conversation_repo = conversation_repo
        self.llm_service = llm_service

    async def _load_history_messages(
        self,
        conversation_id: str,
        conversation: Optional[Conversation],
        exclude_last_user: bool = True
    ) -> Tuple[List, Optional[str]]:
        """
        从数据库加载会话历史消息，采用混合记忆策略

        Args:
            conversation_id: 会话ID
            conversation: 会话对象（用于获取缓存摘要）
            exclude_last_user: 是否排除最后一条用户消息（避免重复发送）

        Returns:
            (LangChain消息列表, 更新的摘要或None)
        """
        try:
            db_messages = await self.message_repo.get_by_conversation(conversation_id)

            if not db_messages:
                return [], None

            # 如果需要排除最后一条用户消息（已作为当前消息传入LLM）
            if exclude_last_user and db_messages:
                last_user_idx = None
                for i in range(len(db_messages) - 1, -1, -1):
                    if db_messages[i].role == MessageRole.USER:
                        last_user_idx = i
                        break
                if last_user_idx is not None:
                    db_messages = db_messages[:last_user_idx]

            # 检查是否需要摘要压缩
            summary = None
            if len(db_messages) > settings.MAX_HISTORY_LENGTH:
                history, summary = await self._build_hybrid_context(
                    db_messages, conversation
                )
            else:
                history = []
                for msg in db_messages:
                    if msg.role == MessageRole.USER:
                        history.append(LCHumanMessage(content=msg.content))
                    elif msg.role == MessageRole.ASSISTANT:
                        history.append(LCAIMessage(content=msg.content))

            logger.info(
                f"从数据库加载了 {len(history)} 条历史消息作为上下文 "
                f"(会话: {conversation_id}, 有摘要: {summary is not None})"
            )
            return history, summary

        except Exception as e:
            logger.error(f"加载历史消息失败: {e}")
            return [], None

    async def _build_hybrid_context(
        self,
        messages: List[Message],
        conversation: Optional[Conversation]
    ) -> Tuple[List, Optional[str]]:
        """
        构建混合上下文：旧消息摘要 + 最近 N 条完整消息

        Args:
            messages: 全部消息列表
            conversation: 会话对象

        Returns:
            (历史消息列表, 更新的摘要)
        """
        # 分割旧消息和最近消息
        old_messages = messages[:-settings.MAX_HISTORY_LENGTH]
        recent_messages = messages[-settings.MAX_HISTORY_LENGTH:]

        # 转换为 LangChain 格式（仅最近消息）
        history = []
        for msg in recent_messages:
            if msg.role == MessageRole.USER:
                history.append(LCHumanMessage(content=msg.content))
            elif msg.role == MessageRole.ASSISTANT:
                history.append(LCAIMessage(content=msg.content))

        # 获取已有摘要
        existing_summary = conversation.summary if conversation else None

        # 生成或更新摘要
        new_summary = await self._generate_summary(
            old_messages=old_messages,
            existing_summary=existing_summary
        )

        if new_summary:
            # 将摘要作为系统消息插入到历史开头
            history.insert(0, LCSystemMessage(content=f"## 历史对话摘要\n\n{new_summary}\n\n请基于以上摘要和以下完整消息来理解上下文。"))
            logger.info(f"生成/更新摘要成功，摘要长度: {len(new_summary)} 字")
        else:
            logger.warning("摘要生成失败，使用空摘要")

        return history, new_summary

    async def _generate_summary(
        self,
        old_messages: List[Message],
        existing_summary: Optional[str] = None
    ) -> Optional[str]:
        """
        调用 LLM 生成摘要

        Args:
            old_messages: 需要压缩的旧消息
            existing_summary: 已有摘要（首次压缩时为空）

        Returns:
            摘要文本或 None
        """
        try:
            from app.core.prompts import format_summary_prompt
            from langchain_core.messages import HumanMessage, SystemMessage

            # 格式化旧消息内容
            old_text = "\n".join(
                f"[{msg.role.value}] {msg.content[:300]}"
                for msg in old_messages
            )

            prompt = format_summary_prompt(
                old_messages=old_text,
                existing_summary=existing_summary or "",
                max_tokens=settings.SUMMARY_MAX_TOKENS
            )

            # 强制使用正确的默认模型生成摘要
            target_model = settings.AGENT_MODEL
            self.llm_service._initialized = False  # 强制重新初始化
            self.llm_service._initialize_llm(target_model)

            # 调用 LLM 生成摘要
            messages = [
                SystemMessage(content="你是一个信息摘要专家，请对以下对话历史进行简洁摘要。"),
                HumanMessage(content=prompt)
            ]
            response = self.llm_service.llm.invoke(messages)
            summary = response.content

            # 确保摘要不超过最大长度
            if len(summary) > settings.SUMMARY_MAX_TOKENS * 2:
                summary = summary[:settings.SUMMARY_MAX_TOKENS * 2]

            return summary

        except Exception as e:
            logger.error(f"摘要生成失败: {e}")
            return None

    async def _update_summary_if_needed(
        self,
        conversation_id: str,
        new_summary: Optional[str]
    ) -> None:
        """
        将摘要保存到数据库

        Args:
            conversation_id: 会话ID
            new_summary: 新摘要
        """
        if new_summary is None:
            return

        try:
            await self.conversation_repo.update_summary(conversation_id, new_summary)
            logger.info(f"摘要已更新到数据库 (会话: {conversation_id})")
        except Exception as e:
            logger.error(f"更新摘要到数据库失败: {e}")

    async def process_message_stream(
        self,
        user_id: str,
        conversation_id: str,
        message: str,
        model_name: Optional[str] = None,
        use_agent: bool = False,
        web_search: bool = False,
        enable_memory: bool = True
    ) -> AsyncGenerator[tuple, None]:
        """
        处理消息并流式返回响应

        Args:
            user_id: 用户ID
            conversation_id: 会话ID
            message: 用户消息
            model_name: 模型名称
            use_agent: 是否使用Agent模式
            web_search: 是否启用联网搜索
            enable_memory: 是否启用长期记忆

        Yields:
            (type, content) 元组
        """
        try:
            # 1. 检索长期记忆
            memories = []
            if enable_memory and settings.MEMORY_ENABLED and settings.MEMORY_AUTO_INJECT:
                memories = await self._retrieve_memories(user_id, message, conversation_id)
                if memories:
                    logger.info(f"Retrieved {len(memories)} memories for context")

            # 2. 构建增强的系统提示词
            system_prompt = await self._build_system_prompt(memories)

            # 3. 保存用户消息到数据库
            user_msg = await self.message_repo.create(
                conversation_id=conversation_id,
                role=MessageRole.USER,
                content=message
            )

            # 4. 获取会话对象（用于读取缓存摘要）
            conversation = await self.conversation_repo.get_by_id(conversation_id)

            # 5. 从数据库加载历史消息（包含摘要压缩逻辑）
            history_messages, new_summary = await self._load_history_messages(
                conversation_id, conversation, exclude_last_user=True
            )

            # 5. 流式调用LLM，传入数据库历史消息作为上下文
            full_response = ""
            async for chunk_type, chunk_content in self.llm_service.chat_stream(
                message=message,
                session_id=conversation_id,
                system_prompt=system_prompt,
                model_name=model_name,
                use_agent=use_agent,
                web_search=web_search,
                history_messages=history_messages
            ):
                if chunk_type == 'content':
                    full_response += chunk_content
                yield (chunk_type, chunk_content)

            # 5. 保存AI响应
            ai_msg = await self.message_repo.create(
                conversation_id=conversation_id,
                role=MessageRole.ASSISTANT,
                content=full_response
            )

            # 6. 更新会话消息计数
            await self.conversation_repo.increment_message_count(conversation_id)

            # 6b. 保存摘要到数据库
            await self._update_summary_if_needed(conversation_id, new_summary)

            # 7. 自动生成标题(如果是第一条消息)
            conversation = await self.conversation_repo.get_by_id(conversation_id)
            if conversation and conversation.message_count == 1:
                # 使用第一条用户消息的前20个字符作为标题
                title = message[:20]
                if len(message) > 20:
                    title += "..."
                await self.conversation_repo.update(conversation_id, title=title)
                logger.info(f"Auto-generated title for conversation {conversation_id}: {title}")

            # 8. 异步存储向量(不阻塞响应)
            if enable_memory and settings.MEMORY_ENABLED:
                asyncio.create_task(
                    self._store_vectors(
                        user_id=user_id,
                        conversation_id=conversation_id,
                        user_message_id=str(user_msg.id),
                        user_content=message,
                        ai_message_id=str(ai_msg.id),
                        ai_content=full_response
                    )
                )

            logger.info(f"Message processed successfully for conversation {conversation_id}")

        except Exception as e:
            logger.error(f"Failed to process message: {e}")
            yield ('error', str(e))

    async def _retrieve_memories(
        self,
        user_id: str,
        query: str,
        conversation_id: str
    ) -> List:
        """
        检索长期记忆

        Args:
            user_id: 用户ID
            query: 查询文本
            conversation_id: 会话ID

        Returns:
            记忆列表
        """
        try:
            memories = await vector_memory_service.retrieve_with_fallback(
                user_id=user_id,
                query=query,
                conversation_id=conversation_id,
                top_k=settings.MEMORY_TOP_K,
                similarity_threshold=settings.MEMORY_SIMILARITY_THRESHOLD
            )
            return memories
        except Exception as e:
            logger.error(f"Failed to retrieve memories: {e}")
            return []

    async def _build_system_prompt(self, memories: List) -> str:
        """
        构建增强的系统提示词

        Args:
            memories: 记忆列表

        Returns:
            系统提示词
        """
        from app.core.prompts import DEFAULT_SYSTEM_PROMPT

        if not memories:
            return DEFAULT_SYSTEM_PROMPT

        # 格式化记忆为上下文
        memory_context = "\n\n## 相关历史记忆\n\n"
        for i, memory in enumerate(memories, 1):
            memory_context += f"{i}. [{memory.role}] {memory.content}\n"

        memory_context += "\n请参考以上相关历史记忆来回答用户的问题。\n"

        # 合并系统提示词
        enhanced_prompt = DEFAULT_SYSTEM_PROMPT + memory_context
        return enhanced_prompt

    async def _store_vectors(
        self,
        user_id: str,
        conversation_id: str,
        user_message_id: str,
        user_content: str,
        ai_message_id: str,
        ai_content: str
    ) -> None:
        """
        异步存储消息向量

        Args:
            user_id: 用户ID
            conversation_id: 会话ID
            user_message_id: 用户消息ID
            user_content: 用户消息内容
            ai_message_id: AI消息ID
            ai_content: AI消息内容
        """
        try:
            # 检查重复
            is_duplicate = await vector_memory_service.check_duplicate(
                user_id=user_id,
                content=user_content
            )

            if not is_duplicate:
                # 存储用户消息向量
                await vector_memory_service.store(
                    user_id=user_id,
                    conversation_id=conversation_id,
                    message_id=user_message_id,
                    content=user_content,
                    role="user"
                )

            # 存储AI响应向量
            await vector_memory_service.store(
                user_id=user_id,
                conversation_id=conversation_id,
                message_id=ai_message_id,
                content=ai_content,
                role="assistant"
            )

            logger.debug(f"Vectors stored for conversation {conversation_id}")

        except Exception as e:
            logger.error(f"Failed to store vectors: {e}")
