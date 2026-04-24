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

        # 生成或更新摘要（同步，但复用已有摘要时跳过）
        new_summary = None
        if existing_summary:
            new_summary = existing_summary
            logger.info("复用已有摘要")
        else:
            # 首次生成：带超时保护，避免 LLM 调用过久
            try:
                new_summary = await asyncio.wait_for(
                    self._generate_summary(
                        old_messages=old_messages,
                        existing_summary=existing_summary,
                    ),
                    timeout=5.0,
                )
                logger.info(f"首次生成摘要成功，摘要长度: {len(new_summary)} 字")
            except asyncio.TimeoutError:
                logger.warning("摘要生成超时，跳过")
            except Exception as e:
                logger.error(f"摘要生成失败: {e}")

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
        enable_memory: bool = True,
        agent_name: Optional[str] = None
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
            agent_name: Agent名称（None=通用助手）

        Yields:
            (type, content) 元组
        """
        try:
            logger.info(f"[消息处理] use_agent={use_agent}, agent_name={agent_name}, enable_memory={enable_memory}")

            # 1. 检索长期记忆（带超时限制，避免阻塞主流程）
            memories = []
            if enable_memory and settings.MEMORY_ENABLED and settings.MEMORY_AUTO_INJECT:
                try:
                    memories = await asyncio.wait_for(
                        self._retrieve_memories(user_id, message, conversation_id),
                        timeout=2.0,
                    )
                    if memories:
                        logger.info(f"Retrieved {len(memories)} memories for context")
                except asyncio.TimeoutError:
                    logger.warning("记忆检索超时，跳过")

            # 2. 格式化记忆上下文
            memory_context = None
            if memories:
                memory_context = "\n\n## 相关历史记忆\n\n"
                for i, memory in enumerate(memories, 1):
                    memory_context += f"{i}. [{memory.role}] {memory.content}\n"
                memory_context += "\n请参考以上相关历史记忆来回答用户的问题。\n"

            # 3. 保存用户消息到数据库
            user_msg = await self.message_repo.create(
                conversation_id=conversation_id,
                role=MessageRole.USER,
                content=message
            )

            # 4. 获取会话对象（用于读取缓存摘要）
            conversation = await self.conversation_repo.get_by_id(conversation_id)

            # 5. 从数据库加载历史消息（包含摘要压缩逻辑，带超时保护）
            history_messages, new_summary = [], None
            try:
                history_messages, new_summary = await asyncio.wait_for(
                    self._load_history_messages(conversation_id, conversation, exclude_last_user=True),
                    timeout=5.0,
                )
                logger.info(f"加载了 {len(history_messages)} 条历史消息，将传给 Agent")
            except asyncio.TimeoutError:
                logger.warning("历史消息加载超时，跳过")

            # 6. 通过 Agent 处理（API endpoint 已做路由，直接使用传入的 agent_name）
            from app.agents import get_agent

            resolved_agent_name = agent_name or "general"
            agent = get_agent(resolved_agent_name)
            logger.info(f"使用 Agent: {resolved_agent_name}")

            # 让 Agent 构建包含记忆的系统提示词
            system_prompt = await agent.build_system_prompt(memory_context)

            full_response = ""
            ai_metadata = {}
            async for chunk_type, chunk_content in agent.process(
                message=message,
                session_id=conversation_id,
                model_name=model_name,
                use_agent=use_agent,
                web_search=web_search,
                context={"system_prompt": system_prompt} if system_prompt else None,
                history_messages=history_messages,
            ):
                if chunk_type == 'content':
                    full_response += chunk_content
                elif chunk_type == 'metadata':
                    try:
                        import json as _json
                        ai_metadata = _json.loads(chunk_content) if isinstance(chunk_content, str) else chunk_content
                    except Exception:
                        pass
                yield (chunk_type, chunk_content)
                # 协作模式下 yield 后立即继续，不等待

            logger.info(f"Agent 处理完成，响应长度: {len(full_response)} 字符")

            # 7. 保存AI响应
            ai_msg = await self.message_repo.create(
                conversation_id=conversation_id,
                role=MessageRole.ASSISTANT,
                content=full_response,
                metadata=ai_metadata if ai_metadata else None,
            )
            logger.info(f"AI响应已保存，消息ID: {ai_msg.id}")

            # 8. 更新会话消息计数
            await self.conversation_repo.increment_message_count(conversation_id)

            # 9. 保存摘要到数据库
            await self._update_summary_if_needed(conversation_id, new_summary)

            # 10. 自动生成标题（如果是第一条消息）
            conversation = await self.conversation_repo.get_by_id(conversation_id)
            if conversation and conversation.message_count == 1:
                title = message[:20] + ("..." if len(message) > 20 else "")
                await self.conversation_repo.update(conversation_id, title=title)
                logger.info(f"自动生成标题: {title}")

            # 11. 存储向量（同步执行，避免 SQLite 锁竞争）
            if enable_memory and settings.MEMORY_ENABLED:
                await self._store_vectors_with_delay(
                    user_id=user_id,
                    conversation_id=conversation_id,
                    user_message_id=str(user_msg.id),
                    user_content=message,
                    ai_content=full_response
                )

            logger.info(f"Message processed successfully for conversation {conversation_id}")

        except Exception as e:
            import traceback
            logger.error(f"Failed to process message: {e}")
            logger.error(f"Traceback: {traceback.format_exc()}")
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

    async def _store_vectors_with_delay(
        self,
        user_id: str,
        conversation_id: str,
        user_message_id: str,
        user_content: str,
        ai_content: str,
    ) -> None:
        """等待 DB 保存完成后存储向量"""
        await asyncio.sleep(1.0)  # 等待 DB 事务完成
        try:
            is_duplicate = await vector_memory_service.check_duplicate(
                user_id=user_id,
                content=user_content
            )
            if not is_duplicate:
                await vector_memory_service.store(
                    user_id=user_id,
                    conversation_id=conversation_id,
                    message_id=user_message_id,
                    content=user_content,
                    role="user"
                )
            await vector_memory_service.store(
                user_id=user_id,
                conversation_id=conversation_id,
                message_id="pending",
                content=ai_content,
                role="assistant"
            )
            logger.debug(f"Vectors stored for conversation {conversation_id}")
        except Exception as e:
            logger.error(f"Failed to store vectors: {e}")


