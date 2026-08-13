"""A2A 服务端 — FC 作为被调用方（一个 agent 引擎 + 多业务域对外暴露）

对齐 A2A 协议标准（Agent Card + Task 状态机 + JSON-RPC 2.0 over HTTP/SSE）：
- GET  /.well-known/agent-card.json — Agent Card 发现（公开，无需鉴权）
- POST /tasks/send             — JSON-RPC 同步提交（阻塞直到完成）
- POST /tasks/sendSubscribe    — SSE 流式（推荐，FC 本就流式输出）
- POST /tasks/get              — 查询任务状态（JSON-RPC）
- POST /tasks/cancel           — 取消任务（JSON-RPC）

鉴权：tasks/* 验 Authorization: Bearer a2a_xxx（SHA256 查表，enabled 才放行）。
作用域：API Key 的 scopes（domain_key 白名单），空 = 无权限。

定位（术语对齐）：FC 对外是「一个 agent」（factory-copilot），内部业务域（domain）
由本体图谱推导；开放哪个域由各 API Key 的 scopes 决定（能力随 Key 单独配置）。每次调用可用 skillId
显式指定业务域，无 skillId 时由 FC 路由在 scopes 白名单内自动选（多业务域协作）。

任务存储为内存字典（同 demo agent），重启丢失；生产需持久化再扩展。
"""
import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.a2a.protocol import (
    AgentCapabilities,
    AgentCard,
    AgentSkill,
    Artifact,
    JSONRPCRequest,
    JSONRPCResponse,
    Part,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from app.core.logger import log
from app.db import get_db
from app.repositories.a2a_api_key_repo import A2aApiKeyRepository
from app.repositories.namespace_config_repo import NamespaceConfigRepository

router = APIRouter(tags=["A2A 服务端"])

# 内存任务存储（同 demo agent，重启丢失；生产需持久化）
_tasks: Dict[str, Task] = {}


def _now() -> str:
    """A2A 协议时间戳（UTC ISO8601）"""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _task_json(task: Task) -> Dict[str, Any]:
    """Task → 纯 dict（枚举转字符串），供 JSON-RPC result / SSE data 序列化"""
    return task.model_dump(mode="json")


def _sse_event(event: str, event_obj: Any, rpc_id: Any) -> str:
    """构造 A2A 标准 SSE 帧：event 行 + data 行（JSON-RPC response 包裹事件对象）。

    事件对象为 TaskStatusUpdateEvent / TaskArtifactUpdateEvent，data 为
    {"jsonrpc":"2.0","id":<回显>,"result":<事件对象>}，对齐 A2A v0.3.0 流式契约。
    """
    data = {"jsonrpc": "2.0", "id": rpc_id, "result": event_obj.model_dump(mode="json")}
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _extract_text(params: Dict[str, Any]) -> str:
    """从 A2A params 提取用户文本（标准 message.parts[kind="text"]）"""
    message = params.get("message")
    if not isinstance(message, dict):
        return ""
    parts = message.get("parts", [])
    if not isinstance(parts, list):
        return ""
    texts: List[str] = []
    for p in parts:
        if isinstance(p, dict) and p.get("kind") == "text":
            texts.append(str(p.get("text", "")))
    return "\n".join(texts)


async def _authenticate(request: Request, db: AsyncSession) -> Dict[str, Any]:
    """Bearer a2a_xxx → SHA256 → 查表 → enabled 才放行。

    返回 {"name": key 备注名, "scopes": domain_key 白名单列表}。
    验签成功更新 last_used_at（可追溯）。
    """
    auth = request.headers.get("Authorization", "")
    token = auth[7:].strip() if auth.startswith("Bearer ") else ""
    if not token:
        raise HTTPException(status_code=401, detail="缺少 Bearer API Key")
    key_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    repo = A2aApiKeyRepository(db)
    record = await repo.get_by_hash(key_hash)
    if not record or not record.enabled:
        raise HTTPException(status_code=401, detail="API Key 无效或已吊销")
    # 更新最近使用时间（失败不阻塞主流程）
    try:
        await repo.update(record.name, last_used_at=datetime.now())
    except Exception:
        pass
    scopes: List[str] = []
    try:
        scopes = json.loads(record.scopes) if record.scopes else []
    except (json.JSONDecodeError, TypeError):
        scopes = []
    return {"name": record.name, "scopes": scopes}


async def _get_active_namespace() -> str:
    """读取活跃 namespace（延迟 import，避免装配期循环依赖）"""
    from app.api.chains import _get_active_namespace as _resolve_ns
    return await _resolve_ns()


async def _open_domain_keys(db: AsyncSession) -> List[str]:
    """所有「已启用 Key」开放的业务域并集（能力声明来源）。

    由各 Key 的 scopes 聚合而来：Key 启停或调整 scopes 即改变 Agent Card skills。
    已删域的 scopes 残留按「域存在性」自动失效，无需清理任务。
    """
    ns = await _get_active_namespace()
    domains = (await NamespaceConfigRepository(db).get(ns, "domains")) or {}
    keys: List[str] = []
    for k in await A2aApiKeyRepository(db).list_all():
        if not k.enabled:
            continue
        try:
            scopes = json.loads(k.scopes) if k.scopes else []
        except (json.JSONDecodeError, TypeError):
            scopes = []
        for s in scopes:
            if isinstance(domains.get(s), dict) and s not in keys:
                keys.append(s)
    return keys


async def _resolve_scopes(
    db: AsyncSession, scopes: List[str], skill_id: Optional[str]
) -> List[str]:
    """校验 skillId 是否在 Key 作用域内，返回 matched_agents（协作池白名单）。

    - scopes 空 = 无权限（最小权限：不配置作用域则不可调用任何业务域）
    - skill_id 显式指定：必须在 scopes 内 + 域仍存在
    - 无 skill_id：返回 scopes（过滤已删域；scopes 空 → 403 无权限）
    """
    ns = await _get_active_namespace()
    domains = (await NamespaceConfigRepository(db).get(ns, "domains")) or {}
    if skill_id:
        if skill_id not in scopes:
            raise HTTPException(status_code=403, detail=f"API Key 无权访问业务域: {skill_id}")
        if not isinstance(domains.get(skill_id), dict):
            raise HTTPException(status_code=404, detail=f"业务域不存在: {skill_id}")
        return [skill_id]
    if not scopes:
        raise HTTPException(status_code=403, detail="API Key 未配置作用域（无权限）")
    allowed = [k for k in scopes if isinstance(domains.get(k), dict)]
    if not allowed:
        raise HTTPException(status_code=403, detail="无可用业务域（作用域内业务域均已失效）")
    return allowed


async def _execute_stream(
    task: Task,
    user_id: str,
    text: str,
    context_id: str,
    matched_agents: List[str],
    rpc_id: Any,
) -> AsyncGenerator[str, None]:
    """执行 FC 内部消息处理，把事件翻译成 A2A v0.3.0 标准 SSE 帧。

    复用 message_service.process_message_stream（use_agent=True 走多业务域协作，
    matched_agents 把协作池限制在作用域白名单内）。
    事件序列：status-update(working) → artifact-update(lastChunk) → status-update(completed/failed, final)。
    """
    from app.repositories.conversation_repository import ConversationRepository
    from app.repositories.message_repository import MessageRepository
    from app.services.message_service import MessageService

    # working 状态（非终态）
    task.status = TaskState(state=TaskStatus.WORKING, timestamp=_now())
    yield _sse_event(
        "status-update",
        TaskStatusUpdateEvent(taskId=task.id, contextId=task.contextId, status=task.status, final=False),
        rpc_id,
    )

    accumulated: List[str] = []
    try:
        async for session in get_db():
            service = MessageService(
                MessageRepository(session), ConversationRepository(session)
            )
            async for etype, econtent in service.process_message_stream(
                user_id=user_id,
                conversation_id=context_id,
                message=text,
                use_agent=True,
                matched_agents=matched_agents,
            ):
                if etype == "content":
                    accumulated.append(str(econtent))
                elif etype == "error":
                    task.status = TaskState(state=TaskStatus.FAILED, timestamp=_now())
                    task.metadata["error"] = str(econtent)[:500]
                    yield _sse_event(
                        "status-update",
                        TaskStatusUpdateEvent(taskId=task.id, contextId=task.contextId, status=task.status, final=True),
                        rpc_id,
                    )
                    return
                elif etype == "done":
                    break
        artifact = Artifact(parts=[Part(kind="text", text="".join(accumulated))])
        task.artifacts = [artifact]
        task.status = TaskState(state=TaskStatus.COMPLETED, timestamp=_now())
        yield _sse_event(
            "artifact-update",
            TaskArtifactUpdateEvent(taskId=task.id, contextId=task.contextId, artifact=artifact, lastChunk=True),
            rpc_id,
        )
        yield _sse_event(
            "status-update",
            TaskStatusUpdateEvent(taskId=task.id, contextId=task.contextId, status=task.status, final=True),
            rpc_id,
        )
    except Exception as e:
        log.error(f"[A2A 服务端] 执行失败: {e}")
        task.status = TaskState(state=TaskStatus.FAILED, timestamp=_now())
        task.metadata["error"] = str(e)[:500]
        yield _sse_event(
            "status-update",
            TaskStatusUpdateEvent(taskId=task.id, contextId=task.contextId, status=task.status, final=True),
            rpc_id,
        )


def _build_task(context_id: str) -> Task:
    """创建 A2A Task 并登记到内存存储（v0.3.0：contextId + TaskState）"""
    task = Task(
        id=str(uuid.uuid4()),
        contextId=context_id,
        status=TaskState(state=TaskStatus.SUBMITTED, timestamp=_now()),
    )
    _tasks[task.id] = task
    return task


# ─────────────────── 端点 ───────────────────

@router.get("/.well-known/agent-card.json")
async def agent_card(request: Request, db: AsyncSession = Depends(get_db)):
    """Agent Card 发现（公开）— skills 从各 Key 开放的能力并集现算，非硬编码。"""
    ns = await _get_active_namespace()
    domains = (await NamespaceConfigRepository(db).get(ns, "domains")) or {}
    # 概念中文标签映射（英文 name → 中文 label，无映射回退原名）
    label_map = {}
    try:
        from app.services.ontology_service import ontology_service
        label_map = ontology_service.get_concept_label_map()
    except Exception:
        pass
    skills: List[AgentSkill] = []
    for key in await _open_domain_keys(db):
        d = domains.get(key)
        if not isinstance(d, dict):
            continue  # 域已删 → 并集里的残留自动失效
        skills.append(AgentSkill(
            id=key,
            name=d.get("display_name", key),
            description=d.get("description", ""),
            tags=[label_map.get(cn, cn) for cn in d.get("concepts", [])][:20],
            inputModes=["text"],
            outputModes=["text"],
            examples=[],
        ))
    card = AgentCard(
        name="factory-copilot",
        description="本体驱动的制造业 AI 引擎（一个 agent，多业务域对外）",
        url=str(request.base_url).rstrip("/"),
        version="1.0.0",
        protocolVersion="0.3.0",
        capabilities=AgentCapabilities(streaming=True, pushNotifications=False, stateTransitionHistory=False),
        defaultInputModes=["text"],
        defaultOutputModes=["text"],
        skills=skills,
        securitySchemes={
            "bearerAuth": {
                "type": "http",
                "scheme": "bearer",
                "description": "API Key（Authorization: Bearer a2a_xxx），通过「能力开放」面板创建",
            }
        },
        security=[{"bearerAuth": []}],
        preferredTransport="JSONRPC",
        endpoints={
            "tasks/send": "/tasks/send",
            "tasks/sendSubscribe": "/tasks/sendSubscribe",
            "tasks/get": "/tasks/get",
            "tasks/cancel": "/tasks/cancel",
        },
    )
    return card.model_dump(mode="json")


@router.post("/tasks/send")
async def tasks_send(
    request: Request, payload: JSONRPCRequest, db: AsyncSession = Depends(get_db)
):
    """JSON-RPC 同步提交 — 阻塞执行直到完成，返回最终 Task。"""
    auth = await _authenticate(request, db)
    text = _extract_text(payload.params)
    context_id = str(payload.params.get("contextId", "") or uuid.uuid4())
    skill_id = payload.params.get("skillId")
    matched_agents = await _resolve_scopes(db, auth["scopes"], skill_id)
    task = _build_task(context_id)
    async for _ in _execute_stream(task, auth["name"], text, context_id, matched_agents, payload.id):
        pass
    return JSONRPCResponse(id=payload.id, result=_task_json(task)).model_dump(mode="json")


@router.post("/tasks/sendSubscribe")
async def tasks_send_subscribe(
    request: Request, payload: JSONRPCRequest, db: AsyncSession = Depends(get_db)
):
    """SSE 流式提交（推荐）— status-update(working) → artifact-update → status-update(completed)。"""
    auth = await _authenticate(request, db)
    text = _extract_text(payload.params)
    context_id = str(payload.params.get("contextId", "") or uuid.uuid4())
    skill_id = payload.params.get("skillId")
    matched_agents = await _resolve_scopes(db, auth["scopes"], skill_id)
    task = _build_task(context_id)
    return StreamingResponse(
        _execute_stream(task, auth["name"], text, context_id, matched_agents, payload.id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@router.post("/tasks/get")
async def tasks_get(
    request: Request, payload: JSONRPCRequest, db: AsyncSession = Depends(get_db)
):
    """查询任务状态（JSON-RPC）"""
    await _authenticate(request, db)
    task_id = str(payload.params.get("id", ""))
    task = _tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return JSONRPCResponse(id=payload.id, result=_task_json(task)).model_dump(mode="json")


@router.post("/tasks/cancel")
async def tasks_cancel(
    request: Request, payload: JSONRPCRequest, db: AsyncSession = Depends(get_db)
):
    """取消任务（仅 working/submitted 态可取消）"""
    await _authenticate(request, db)
    task_id = str(payload.params.get("id", ""))
    task = _tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.status.state in (TaskStatus.WORKING, TaskStatus.SUBMITTED):
        task.status = TaskState(state=TaskStatus.CANCELED, timestamp=_now())
    return JSONRPCResponse(id=payload.id, result=_task_json(task)).model_dump(mode="json")
