"""
会话管理API
提供会话的CRUD接口
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.schemas import (
    ConversationCreate,
    ConversationListResponse,
    ConversationResponse,
    ConversationUpdate,
    MessageListResponse,
    MessageResponse,
)
from app.repositories.conversation_repository import ConversationRepository
from app.repositories.message_repository import MessageRepository
from app.services.conversation_service import ConversationService

router = APIRouter(tags=["会话管理"])


# 依赖注入
# 模块级引擎和会话工厂，应用启动时创建一次

_engine = None
_async_session = None


async def get_db():
    """获取数据库会话（延迟初始化，复用全局引擎）"""
    global _engine, _async_session
    if _engine is None:
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        _engine = create_async_engine(settings.DATABASE_URL, echo=False)
        _async_session = async_sessionmaker(_engine, expire_on_commit=False)
    async with _async_session() as session:
        yield session


def get_conversation_service(db: AsyncSession = Depends(get_db)) -> ConversationService:
    """获取会话服务实例"""
    conversation_repo = ConversationRepository(db)
    message_repo = MessageRepository(db)
    return ConversationService(conversation_repo, message_repo)


def get_current_user_id(request: Request) -> str:
    """从请求 Header 解析当前用户 ID。

    优先级: X-User-Id > Bearer token 会话映射 > default_user。
    """
    user_id = request.headers.get("X-User-Id", "").strip()
    if user_id:
        return user_id
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:]
        from app.services.auth_service import auth_service as _auth_svc
        user_id = _auth_svc.resolve_user(token)
        if user_id:
            return user_id
    return "default_user"


@router.post("", response_model=ConversationResponse, summary="创建会话")
async def create_conversation(
    request: ConversationCreate,
    service: ConversationService = Depends(get_conversation_service),
    user_id: str = Depends(get_current_user_id)
):
    """
    创建一个新的对话会话。

    - **title**: 会话标题（可选，不填则自动生成）
    - **metadata**: 扩展元数据（可选）
    """
    try:
        conversation = await service.create(
            user_id=user_id,
            title=request.title,
            metadata=request.metadata
        )
        return _conversation_to_response(conversation)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("", response_model=ConversationListResponse, summary="获取会话列表")
async def get_conversations(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    search: Optional[str] = Query(None, description="按标题搜索关键词"),
    is_active: Optional[bool] = Query(None, description="是否激活"),
    service: ConversationService = Depends(get_conversation_service),
    user_id: str = Depends(get_current_user_id)
):
    """
    分页获取当前用户的会话列表，支持搜索和过滤。
    """
    try:
        conversations, total = await service.get_list(
            user_id=user_id,
            page=page,
            page_size=page_size,
            search=search,
            is_active=is_active
        )
        return ConversationListResponse(
            conversations=[_conversation_to_response(c) for c in conversations],
            total=total,
            page=page,
            page_size=page_size
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{conversation_id}", response_model=ConversationResponse, summary="获取会话详情")
async def get_conversation(
    conversation_id: str,
    service: ConversationService = Depends(get_conversation_service)
):
    """
    根据 ID 获取单个会话的详细信息。
    """
    conversation = await service.get_by_id(conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return _conversation_to_response(conversation)


@router.put("/{conversation_id}", response_model=ConversationResponse, summary="更新会话")
async def update_conversation(
    conversation_id: str,
    request: ConversationUpdate,
    service: ConversationService = Depends(get_conversation_service)
):
    """
    更新会话信息，目前支持修改标题。

    - **conversation_id**: 会话 ID
    - **title**: 新标题
    """
    conversation = await service.get_by_id(conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    try:
        conversation = await service.update(
            conversation_id,
            title=request.title,
            is_active=request.is_active,
            metadata=request.metadata,
        )

        return _conversation_to_response(conversation)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{conversation_id}", summary="删除会话")
async def delete_conversation(
    conversation_id: str,
    service: ConversationService = Depends(get_conversation_service)
):
    """
    删除指定会话及其所有消息（同时删除关联的向量记忆）。
    """
    conversation = await service.get_by_id(conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    try:
        deleted = await service.delete(conversation_id)
        if not deleted:
            raise HTTPException(status_code=500, detail="Failed to delete conversation")
        return {"message": "Conversation deleted successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{conversation_id}/messages", response_model=MessageListResponse, summary="获取会话消息列表")
async def get_conversation_messages(
    conversation_id: str,
    limit: Optional[int] = Query(None, description="返回消息数量"),
    offset: int = Query(0, ge=0, description="偏移量"),
    service: ConversationService = Depends(get_conversation_service)
):
    """
    获取指定会话的消息列表，按时间升序排列。
    """
    conversation = await service.get_by_id(conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    try:
        messages = await service.get_messages(
            conversation_id=conversation_id,
            limit=limit,
            offset=offset
        )
        return MessageListResponse(
            messages=[_message_to_response(m) for m in messages],
            total=len(messages)
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _conversation_to_response(conversation: Conversation) -> ConversationResponse:
    """转换Conversation为响应模型"""
    return ConversationResponse(
        id=str(conversation.id),
        user_id=conversation.user_id,
        title=conversation.title,
        message_count=conversation.message_count,
        is_active=conversation.is_active,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        metadata=conversation.metadata_dict
    )


def _message_to_response(message: Message) -> MessageResponse:
    """转换Message为响应模型"""
    return MessageResponse(
        id=str(message.id),
        conversation_id=str(message.conversation_id),
        role=message.role,
        content=message.content,
        created_at=message.created_at,
        metadata=message.metadata_dict
    )
