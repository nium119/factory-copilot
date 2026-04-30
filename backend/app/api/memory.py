"""
记忆管理API
提供长期记忆的检索和管理接口
"""
from fastapi import APIRouter, Depends, HTTPException

from app.core.config import settings
from app.models.schemas import MemoryConfig, MemoryRetrieveRequest, MemoryRetrieveResponse
from app.services.vector_memory_service import vector_memory_service

router = APIRouter(prefix="/memory", tags=["记忆管理"])


def get_current_user_id() -> str:
    """获取当前用户 ID（临时实现，默认 default_user）"""
    return "default_user"


@router.post("/retrieve", response_model=MemoryRetrieveResponse, summary="检索记忆")
async def retrieve_memory(
    request: MemoryRetrieveRequest,
    user_id: str = Depends(get_current_user_id)
):
    """
    基于向量相似度检索相关长期记忆。

    - **query**: 查询文本
    - **conversation_id**: 限定会话范围（可选）
    - **top_k**: 返回结果数量
    - **similarity_threshold**: 最低相似度阈值
    """
    try:
        memories = await vector_memory_service.retrieve_with_fallback(
            user_id=user_id,
            query=request.query,
            conversation_id=request.conversation_id,
            top_k=request.top_k,
            similarity_threshold=request.similarity_threshold
        )
        return MemoryRetrieveResponse(
            memories=memories,
            total=len(memories)
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/config", response_model=MemoryConfig, summary="获取记忆配置")
async def get_memory_config():
    """
    返回当前长期记忆的配置参数。
    """
    return MemoryConfig(
        enabled=settings.MEMORY_ENABLED,
        top_k=settings.MEMORY_TOP_K,
        similarity_threshold=settings.MEMORY_SIMILARITY_THRESHOLD,
        auto_inject=settings.MEMORY_AUTO_INJECT
    )


@router.put("/config", response_model=MemoryConfig, summary="更新记忆配置")
async def update_memory_config(config: MemoryConfig):
    """
    更新长期记忆的配置参数。

    - **enabled**: 是否启用记忆
    - **top_k**: 检索数量
    - **similarity_threshold**: 相似度阈值
    - **auto_inject**: 是否自动注入上下文
    """
    # 更新运行时配置（进程重启后恢复为 .env 默认值）
    settings.MEMORY_ENABLED = config.enabled
    settings.MEMORY_TOP_K = config.top_k
    settings.MEMORY_SIMILARITY_THRESHOLD = config.similarity_threshold
    settings.MEMORY_AUTO_INJECT = config.auto_inject
    return MemoryConfig(
        enabled=settings.MEMORY_ENABLED,
        top_k=settings.MEMORY_TOP_K,
        similarity_threshold=settings.MEMORY_SIMILARITY_THRESHOLD,
        auto_inject=settings.MEMORY_AUTO_INJECT,
    )


@router.delete("/conversation/{conversation_id}", summary="删除会话记忆")
async def delete_conversation_memory(
    conversation_id: str
):
    """
    删除指定会话的所有向量记忆（Milvus 数据）。
    """
    try:
        deleted = await vector_memory_service.delete_by_conversation(conversation_id)
        if not deleted:
            raise HTTPException(
                status_code=500,
                detail="Failed to delete conversation memory"
            )
        return {"message": "Conversation memory deleted successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
