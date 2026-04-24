"""
消息API
提供消息发送接口，支持 Agent 路由
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from typing import Optional
import json
import asyncio

from app.core.config import settings
from app.core.logger import log
from app.repositories.conversation_repository import ConversationRepository
from app.repositories.message_repository import MessageRepository
from app.services.message_service import MessageService
from app.services.conversation_service import ConversationService
from app.agents import get_agents_from_db

router = APIRouter(prefix="/messages", tags=["消息"])


class SendMessageRequest(BaseModel):
    """发送消息请求"""
    conversation_id: str
    content: str
    model_name: Optional[str] = None
    agent_name: Optional[str] = None
    use_agent: bool = False
    web_search: bool = False
    enable_memory: bool = True
    enable_thinking: Optional[bool] = None


# 模块级引擎和会话工厂，应用启动时创建一次
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

_engine = create_async_engine(settings.DATABASE_URL, echo=False)
_async_session = async_sessionmaker(_engine, expire_on_commit=False)


async def get_db() -> AsyncSession:
    """获取数据库会话（复用全局引擎）"""
    async with _async_session() as session:
        yield session


def get_message_service(db: AsyncSession = Depends(get_db)) -> MessageService:
    """获取消息服务实例"""
    message_repo = MessageRepository(db)
    conversation_repo = ConversationRepository(db)
    return MessageService(message_repo, conversation_repo)


def get_conversation_service(db: AsyncSession = Depends(get_db)) -> ConversationService:
    """获取会话服务实例"""
    conversation_repo = ConversationRepository(db)
    message_repo = MessageRepository(db)
    return ConversationService(conversation_repo, message_repo)


def get_current_user_id() -> str:
    """获取当前用户 ID（临时实现，默认 default_user）"""
    return "default_user"


@router.get("/agents", summary="获取可用 Agent 列表")
async def list_agents():
    """从数据库获取所有已注册的 Agent 元信息"""
    return get_agents_from_db()


@router.post("/stream", summary="流式发送消息")
async def send_message_stream(
    request: SendMessageRequest,
    message_service: MessageService = Depends(get_message_service),
    user_id: str = Depends(get_current_user_id)
):
    """
    通过 SSE 流式发送消息并逐步接收 AI 回复。

    返回的 SSE 事件类型：
    - **agent_info**: Agent 元信息（首次发送）
    - **content**: AI 回复的文本片段
    - **thinking**: 模型思考过程
    - **error**: 错误信息
    - **[DONE]**: 传输完成标记
    """

    async def event_generator():
        """SSE事件生成器"""
        try:
            from app.agents.router import route_intent
            from app.agents import get_agent

            route = await route_intent(request.content, request.agent_name)
            agent_name = route["agent_name"]
            use_agent = route["use_agent"]
            matched_agents = route.get("matched_agents", [])

            # 资源感知：根据查询复杂度自动选择模型
            from app.agents.router import select_model_for_complexity
            model_name = select_model_for_complexity(request.content, request.model_name)
            if model_name:
                log.info(f"[资源优化] 自动选择模型: {model_name} (基于查询复杂度)")
            else:
                model_name = request.model_name

            agent = get_agent(agent_name)
            agent_info = agent.get_info()

            # 发送 Agent 信息
            log.info(f"[SSE] yield agent_info: {agent_info['display_name']}")
            yield f"data: {json.dumps({'type': 'agent_info', 'agent_name': agent_info['name'], 'display_name': agent_info['display_name'], 'icon': agent_info['icon'], 'color': agent_info['color']})}\n\n"

            # 通过 MessageService 处理消息（包含记忆、数据库持久化）
            # 传递已路由的 agent_name，避免 service 层重复路由（LLM 调用导致延迟）
            async for chunk_type, chunk_content in message_service.process_message_stream(
                user_id=user_id,
                conversation_id=request.conversation_id,
                message=request.content,
                model_name=model_name,
                use_agent=use_agent,
                web_search=request.web_search,
                enable_memory=request.enable_memory,
                agent_name=agent_name,
                enable_thinking=request.enable_thinking,
                matched_agents=matched_agents,
            ):
                log.info(f"[SSE] yield chunk_type={chunk_type}, content_len={len(str(chunk_content))}")
                yield f"data: {json.dumps({'type': chunk_type, 'content': chunk_content})}\n\n"

            # 发送结束标记
            log.info("[SSE] yield [DONE]")
            yield "data: [DONE]\n\n"

        except Exception as e:
            error_data = json.dumps({
                "type": "error",
                "content": str(e)
            })
            yield f"data: {error_data}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )
