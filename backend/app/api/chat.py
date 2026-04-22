from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from app.models.schemas import ChatMessage, AgentResponse, SessionInfo
from app.services.agent_service import agent_service
from app.services.llm_service import llm_service
from app.core.model_config import MODEL_PROVIDERS
from app.core.logger import log
from typing import List
import json
import asyncio

router = APIRouter(prefix="/chat", tags=["聊天"])

@router.get("/models", summary="获取可用模型列表")
async def get_models():
    """
    获取当前系统支持的所有 AI 模型列表。

    返回包含模型名称、提供商、是否支持深度思考等信息。
    """
    models = []
    for provider, config in MODEL_PROVIDERS.items():
        for model_key, model_info in config["models"].items():
            models.append({
                "key": model_key,
                "label": model_info["name"],
                "provider": provider,
                "enable_thinking": model_info.get("enable_thinking", False)
            })
    return models

@router.post("/", response_model=AgentResponse, summary="Agent 对话（非流式）")
async def chat(message: ChatMessage):
    """
    与 AI Agent 进行单次对话，返回完整响应。

    - **content**: 用户输入内容
    - **session_id**: 会话标识（可选，默认 default）
    - **model_name**: 指定模型名称（可选）
    - **use_agent**: 是否启用 Agent 模式
    - **web_search**: 是否启用联网搜索
    """
    try:
        session_id = message.session_id or "default"

        response = await agent_service.process_message(
            content=message.content,
            session_id=session_id
        )

        return AgentResponse(
            response=response,
            session_id=session_id,
            status="success"
        )
    except Exception as e:
        log.error(f"聊天接口错误: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/stream", summary="Agent 对话（流式）")
async def chat_stream(message: ChatMessage):
    """
    与 AI Agent 进行流式对话，通过 SSE 逐步返回响应内容。

    返回的 SSE 事件包含两种类型：
    - **thinking**: 模型的思考过程（深度思考模式）
    - **content**: 模型的最终回复内容
    """
    async def generate():
        try:
            session_id = message.session_id or "default"
            
            # 流式调用LLM
            async for chunk_type, chunk_content in llm_service.chat_stream(
                message=message.content,
                session_id=session_id,
                model_name=message.model_name,
                use_agent=message.use_agent,
                web_search=message.web_search
            ):
                if chunk_type == 'thinking':
                    # 发送思考过程
                    yield f"data: {json.dumps({'type': 'thinking', 'content': chunk_content})}\n\n"
                elif chunk_type == 'content':
                    # 发送回复内容
                    yield f"data: {json.dumps({'type': 'content', 'content': chunk_content})}\n\n"
            
            # 发送完成信号
            yield f"data: {json.dumps({'type': 'done', 'session_id': session_id})}\n\n"
            
        except Exception as e:
            log.error(f"流式聊天错误: {str(e)}")
            yield f"data: {json.dumps({'type': 'error', 'content': str(e)})}\n\n"
    
    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )

@router.get("/history/{session_id}", response_model=List[dict], summary="获取会话历史")
async def get_history(session_id: str):
    """
    获取指定会话的内存中的对话历史（非数据库持久化数据）。

    - **session_id**: 会话标识
    """
    history = await agent_service.get_session_history(session_id)
    return history

@router.delete("/session/{session_id}", summary="清除会话")
async def clear_session(session_id: str):
    """
    清除指定会话的内存中的对话历史（非数据库持久化数据）。

    - **session_id**: 会话标识
    """
    success = await agent_service.clear_session(session_id)
    return {"success": success, "session_id": session_id}
