"""
消息API
提供消息发送接口，支持 Agent 路由
"""
import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.agents import get_agents_from_db
from app.core.config import settings
from app.core.logger import log
from app.repositories.conversation_repository import ConversationRepository
from app.repositories.message_repository import MessageRepository
from app.services.conversation_service import ConversationService
from app.services.message_service import MessageService

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


def get_current_user_id(request: Request) -> str:
    """从请求 Header 解析当前用户 ID，同时设置 JWT claims ContextVar。

    优先级: X-User-Id > Bearer token (JWT) 会话映射 > default_user。
    """
    from app.services.multi_system_backend import _request_claims, _parse_jwt_claims
    # 前端登录后通过 X-User-Id 直接传递用户标识
    user_id = request.headers.get("X-User-Id", "").strip()
    if user_id:
        return user_id
    # 回退: 从 Bearer token 解析 JWT claims
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:]
        # 尝试解析 JWT claims
        claims = _parse_jwt_claims(token)
        if claims:
            _request_claims.set(claims)
            user_id = claims.get("sub") or claims.get("userId") or claims.get("nameid", "")
            if user_id:
                return user_id
        # 兼容旧 session 映射
        from app.services.auth_service import auth_service as _auth_svc
        user_id = _auth_svc.resolve_user(token)
        if user_id:
            return user_id
    return "default_user"


class ConfirmRequest(BaseModel):
    """确认请求"""
    approved: bool = True
    params: Optional[dict] = None


class PendingQuery(BaseModel):
    """待办查询参数"""
    user_id: str = ""
    user_roles: str = ""  # 逗号分隔的角色列表


class ApprovalRequest(BaseModel):
    """审批请求"""
    user_id: str = ""
    comment: str = ""


@router.post("/confirm/{session_id}", summary="确认或取消写操作")
async def confirm_action(session_id: str, request: ConfirmRequest):
    """前端确认/取消高危操作。

    后端 _standard_process 在执行 requiresConfirmation 的 Action 前
    会挂起等待此端点。超时 60s 自动取消。
    """
    from app.agents.base import BaseAgent
    resolved = BaseAgent.resolve_confirmation(session_id, request.approved, request.params)
    return {"resolved": resolved, "session_id": session_id, "approved": request.approved}


@router.get("/agents", summary="获取可用 Agent 列表")
async def list_agents():
    """从数据库获取所有已注册的 Agent 元信息"""
    from app.db import get_db
    from app.repositories.agent_repository import AgentRepository
    async for session in get_db():
        repo = AgentRepository(session)
        agents = await repo.get_enabled_agents()
        result = []
        for a in agents:
            try:
                from app.agents import get_agent
                agent = get_agent(a.name)
                info = agent.get_info()
                info["enabled"] = a.enabled
                result.append(info)
            except (KeyError, Exception):
                result.append({
                    "name": a.name, "display_name": a.display_name,
                    "icon": a.icon, "color": a.color,
                    "description": a.description, "enabled": a.enabled,
                })
        return result


@router.post("/stream", summary="流式发送消息")
async def send_message_stream(
    request: SendMessageRequest,
    http_request: Request,
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

    # 从请求头提取 MES token，传递给 CLI 子进程
    mes_token = None
    auth_header = http_request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        mes_token = auth_header[7:]

    async def event_generator():
        """SSE事件生成器"""
        # 设置 API 调用日志的请求上下文
        from app.services.multi_system_backend import _request_user_id, _request_conversation_id, _request_message
        _request_user_id.set(user_id or "")
        _request_conversation_id.set(request.conversation_id)
        _request_message.set(request.content[:200])
        try:
            from app.agents import get_agent
            from app.agents.router import route_intent
            from app.agents.tools.mes_cli_runner import set_token

            set_token(mes_token)

            route = await route_intent(request.content, request.agent_name)
            agent_name = route["agent_name"]
            use_agent = route["use_agent"]
            matched_agents = route.get("matched_agents", [])

            # 用户偏好适应
            if route["confidence"] < 0.9:
                try:
                    async with _async_session() as adapt_db:
                        from app.services.adaptation_service import get_adapted_confidence
                        adapted_confidence = await get_adapted_confidence(
                            adapt_db, user_id, agent_name, route["confidence"]
                        )
                        if adapted_confidence < 0.3:
                            log.info(
                                f"[Adaptation] 用户偏好负向，{agent_name} 置信度过低 ({adapted_confidence})，"
                                f"回退到 analysis_monitor"
                            )
                            agent_name = "analysis_monitor"
                            use_agent = False
                except Exception as e:
                    log.debug(f"[Adaptation] 偏好调整跳过: {e}")

            # 资源感知：根据查询复杂度自动选择模型
            from app.agents.router import select_model_for_complexity
            model_name = select_model_for_complexity(request.content, request.model_name)
            if model_name:
                log.info(f"[资源优化] 自动选择模型: {model_name} (基于查询复杂度)")
            else:
                model_name = request.model_name

            try:
                agent = get_agent(agent_name)
            except KeyError:
                log.warning(f"[SSE] Agent '{agent_name}' 不可用（无域配置）")
                yield f"data: {json.dumps({'type': 'error', 'content': '当前没有可用 Agent，请先配置业务域'})}\n\n"
                yield "data: [DONE]\n\n"
                return
            agent_info = agent.get_info()

            # 发送 Agent 信息
            log.info(f"[SSE] 发送 agent_info: {agent_info['display_name']}")
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
                log.info(f"[SSE] 发送 chunk_type={chunk_type}, content_len={len(str(chunk_content))}")
                yield f"data: {json.dumps({'type': chunk_type, 'content': chunk_content})}\n\n"

            # 发送数据来源信息 + 恢复建议
            from app.agents.tools.mes_cli_runner import get_data_source, get_error_recovery_hint
            ds = get_data_source()
            hint = get_error_recovery_hint() if ds == "mock_fallback" else None
            yield f"data: {json.dumps({'type': 'data_source', 'source': ds, 'hint': hint})}\n\n"

            # 发送结束标记
            log.info("[SSE] 发送 [DONE]")
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


@router.get("/pending")
async def get_pending_confirmations(
    user_id: str = "",
    user_roles: str = "",
    db: AsyncSession = Depends(get_db),
):
    """获取当前用户可审批的待办消息列表。"""
    from app.repositories.message_repository import MessageRepository
    repo = MessageRepository(db)

    # 解析用户角色
    roles = [r.strip() for r in user_roles.split(",") if r.strip()] if user_roles else []

    # 查询待审批消息
    all_pending = await repo.get_pending_confirmations(assigned_to=None, limit=100)

    # 过滤：无角色时显示所有待审批；有角色时只显示匹配的
    result = []
    for msg in all_pending:
        assigned = msg.assigned_to or ""
        if not roles or not assigned or assigned in roles:
            content_data = {}
            try:
                content_data = json.loads(msg.content) if msg.content else {}
            except Exception:
                content_data = {"raw": msg.content}
            result.append({
                "id": msg.id,
                "conversation_id": msg.conversation_id,
                "action_label": content_data.get("action_label", ""),
                "concept_label": content_data.get("concept_label", ""),
                "tool": content_data.get("tool", ""),
                "params": content_data.get("params", {}),
                "param_schema": content_data.get("param_schema", []),
                "risk": content_data.get("risk", "write"),
                "context": content_data.get("context", {}),
                "user_id": content_data.get("user_id", ""),
                "message": content_data.get("message", ""),
                "assigned_to": assigned,
                "created_at": str(msg.created_at) if msg.created_at else "",
            })

    return {"pending": result, "total": len(result)}


@router.post("/{message_id}/approve")
async def approve_confirmation(
    message_id: str,
    body: ApprovalRequest,
    db: AsyncSession = Depends(get_db),
):
    """通过审批并执行原始动作。"""
    from app.repositories.message_repository import MessageRepository
    from app.models.message import MessageType, ConfirmStatus, MessageRole

    repo = MessageRepository(db)
    pending_msg = await repo.get_by_id(message_id)
    if not pending_msg:
        raise HTTPException(status_code=404, detail=f"消息不存在: {message_id}")
    if pending_msg.status != ConfirmStatus.PENDING.value:
        raise HTTPException(status_code=400, detail="该消息已处理")

    # 提取原始动作参数
    content_data = {}
    try:
        content_data = json.loads(pending_msg.content) if pending_msg.content else {}
    except Exception:
        pass
    tool_name = content_data.get("tool", "")
    params = content_data.get("params", {})
    original_user_id = content_data.get("user_id", "")
    conversation_id = pending_msg.conversation_id

    # 标记为已审批
    updated = await repo.resolve_confirmation(message_id, approved=True, reviewed_by=body.user_id)
    log.info(f"[审批] message_id={message_id} 已通过, 开始执行动作 {tool_name}")

    # 执行原始动作
    exec_result = {"success": False, "message": "未执行", "rowCount": 0}
    if tool_name:
        try:
            from app.services.action_executor import action_executor
            exec_result = await action_executor.execute_structured_async(
                tool_name, params, user_id=original_user_id or body.user_id,
            )
            log.info(f"[审批] 动作 {tool_name} 执行完成: rowCount={exec_result.get('rowCount', 0)}")
        except Exception as e:
            log.error(f"[审批] 动作执行失败: {e}")
            exec_result = {"success": False, "message": str(e), "rowCount": 0}

    # 保存执行结果到对话
    result_content = _format_exec_result(tool_name, content_data, exec_result)
    await repo.create(
        conversation_id=conversation_id,
        role=MessageRole.ASSISTANT,
        content=result_content,
        message_type=MessageType.REPORT.value if exec_result.get("rowCount", 0) > 0 else MessageType.INFO.value,
    )

    # 处理推理链确认（如果执行结果触发了推理规则需要确认）
    inferences = exec_result.get("inferences", []) or []
    if exec_result.get("needs_inference_confirmation") and inferences:
        for inf in inferences:
            await repo.create(
                conversation_id=conversation_id,
                role=MessageRole.SYSTEM,
                content=json.dumps({
                    "tool": inf.get("target_action", tool_name),
                    "action_label": inf.get("rule_label", ""),
                    "concept_label": inf.get("target_concept", ""),
                    "params": inf.get("target_params", {}),
                    "risk": "inference",
                    "user_id": body.user_id,
                    "message": f"推理链: {inf.get('description', '')}",
                }, ensure_ascii=False),
                message_type=MessageType.CONFIRM.value,
                status=ConfirmStatus.PENDING.value,
                assigned_to=content_data.get("assigned_to", ""),
            )
            log.info(f"[审批] 推理链确认已写入: {inf.get('rule_label', '')}")

    return {
        "success": True,
        "message_id": message_id,
        "status": updated.status,
        "exec_result": {"rowCount": exec_result.get("rowCount", 0), "message": exec_result.get("message", "")},
    }


def _format_exec_result(tool_name: str, content_data: dict, exec_result: dict) -> str:
    """格式化执行结果为对话消息。"""
    parts = []
    action_label = content_data.get("action_label", tool_name)
    concept_label = content_data.get("concept_label", "")
    parts.append(f"✅ 已执行: **{action_label}**")
    if concept_label:
        parts[0] += f" → {concept_label}"
    if exec_result.get("success"):
        row_count = exec_result.get("rowCount", 0)
        parts.append(f"影响行数: {row_count}" if row_count > 0 else "操作成功")
    else:
        parts.append(f"执行结果: {exec_result.get('message', '完成')}")
    return "\n".join(parts)


@router.post("/{message_id}/reject")
async def reject_confirmation(
    message_id: str,
    body: ApprovalRequest,
    db: AsyncSession = Depends(get_db),
):
    """拒绝审批。"""
    from app.repositories.message_repository import MessageRepository
    repo = MessageRepository(db)
    updated = await repo.resolve_confirmation(message_id, approved=False, reviewed_by=body.user_id)
    if not updated:
        raise HTTPException(status_code=404, detail=f"消息不存在: {message_id}")
    return {"success": True, "message_id": message_id, "status": updated.status}
