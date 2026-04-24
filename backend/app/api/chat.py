from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from app.models.schemas import ChatMessage, AgentResponse, SessionInfo
from app.services.agent_service import agent_service
from app.services.llm_service import llm_service
from app.core.model_config import MODEL_PROVIDERS
from app.core.logger import log
from app.agents.router import route_intent
from app.agents import get_agent, get_all_agents
from typing import List
import json
import asyncio

router = APIRouter(prefix="/chat", tags=["聊天"])

@router.get("/models", summary="获取可用模型列表")
async def get_models():
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

@router.get("/agents", summary="获取可用 Agent 列表")
async def get_agents():
    """返回所有已注册的 Agent 元信息"""
    agents = get_all_agents()
    result = []
    for name, agent in agents.items():
        info = agent.get_info()
        result.append(info)
    return result

@router.post("/", response_model=AgentResponse, summary="Agent 对话（非流式）")
async def chat(message: ChatMessage):
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
    流式对话，支持 Agent 自动路由。

    - 如果 agent_name 指定了具体 Agent，直接路由到该 Agent
    - 如果 agent_name="auto"，根据意图自动识别
    - 如果 agent_name 为空或 "general"，使用通用助手
    """
    async def generate():
        try:
            session_id = message.session_id or "default"

            # 1. Agent 路由
            route = await route_intent(message.content, message.agent_name)
            agent_name = route["agent_name"]
            agent = get_agent(agent_name)
            agent_info = agent.get_info()

            # 2. 发送 Agent 信息
            yield f"data: {json.dumps({'type': 'agent_info', 'agent_name': agent_info['name'], 'display_name': agent_info['display_name'], 'icon': agent_info['icon'], 'color': agent_info['color']})}\n\n"

            # 3. 调用 Agent 处理
            async for chunk_type, chunk_content in agent.process(
                message=message.content,
                session_id=session_id,
                model_name=message.model_name,
                use_agent=message.use_agent,
                web_search=message.web_search,
            ):
                yield f"data: {json.dumps({'type': chunk_type, 'content': chunk_content})}\n\n"

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
    history = await agent_service.get_session_history(session_id)
    return history

@router.delete("/session/{session_id}", summary="清除会话")
async def clear_session(session_id: str):
    success = await agent_service.clear_session(session_id)
    return {"success": success, "session_id": session_id}
