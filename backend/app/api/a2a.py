"""A2A API — 外部 Agent 连接管理 + 任务委托 + 状态"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.a2a import A2AError, a2a_registry
from app.core.logger import log
from app.db import get_db
from app.repositories.a2a_agent_repo import A2aAgentRepository

router = APIRouter(prefix="/a2a", tags=["A2A"])


# ---------- Pydantic schemas ----------

class DelegateIn(BaseModel):
    message: str
    session_id: str = ""


# ---------- 连接管理 ----------

@router.post("/agents/{name}/connect", summary="连接外部 Agent（获取 Agent Card）")
async def connect_agent(name: str, db: AsyncSession = Depends(get_db)):
    repo = A2aAgentRepository(db)
    row = await repo.get_by_name(name)
    if not row:
        raise HTTPException(404, f"Agent 不存在: {name}")
    if not row.url or not row.url.strip():
        raise HTTPException(400, f"Agent '{name}' 未配置 URL")
    try:
        client = await a2a_registry.connect_agent(name, row.url.strip(), auto_collab=row.auto_collab)
    except A2AError as e:
        raise HTTPException(502, str(e)) from e
    return {"ok": True, "name": name, "connected": True,
            "card": client.agent_card.model_dump() if client.agent_card else None}


@router.post("/agents/{name}/disconnect", summary="断开外部 Agent")
async def disconnect_agent(name: str):
    await a2a_registry.close_agent(name)
    return {"ok": True, "name": name, "connected": False}


@router.post("/agents/apply", summary="连接所有启用且配置了 URL 的外部 Agent")
async def apply_agents(db: AsyncSession = Depends(get_db)):
    repo = A2aAgentRepository(db)
    rows = await repo.list_enabled()
    connected, failed = [], []
    for row in rows:
        if not row.url or not row.url.strip():
            continue
        try:
            await a2a_registry.connect_agent(row.name, row.url.strip(), auto_collab=row.auto_collab)
            connected.append(row.name)
        except Exception as e:
            failed.append({"name": row.name, "error": str(e)})
            log.warning(f"[A2A] 批量连接失败 {row.name}: {e}")
    return {"ok": True, "connected": connected, "failed": failed, "total": len(rows)}


# ---------- 任务委托 ----------

@router.post("/delegate/{agent_name}", summary="委托任务给外部 Agent")
async def delegate(agent_name: str, body: DelegateIn):
    """向指定外部 Agent 发送任务，返回最终 Task（阻塞到完成/失败）"""
    if not a2a_registry.is_connected(agent_name):
        raise HTTPException(409, f"外部 Agent '{agent_name}' 未连接，请先连接")
    try:
        task = await a2a_registry.send_task(agent_name, body.message, session_id=body.session_id)
    except A2AError as e:
        raise HTTPException(502, str(e)) from e
    return task.model_dump()


@router.get("/tasks/{agent_name}/{task_id}", summary="查询外部 Agent 任务状态")
async def get_task(agent_name: str, task_id: str):
    if not a2a_registry.is_connected(agent_name):
        raise HTTPException(409, f"外部 Agent '{agent_name}' 未连接")
    try:
        task = await a2a_registry.get_task(agent_name, task_id)
    except A2AError as e:
        raise HTTPException(502, str(e)) from e
    return task.model_dump()


@router.post("/tasks/{agent_name}/{task_id}/cancel", summary="取消外部 Agent 任务")
async def cancel_task(agent_name: str, task_id: str):
    if not a2a_registry.is_connected(agent_name):
        raise HTTPException(409, f"外部 Agent '{agent_name}' 未连接")
    try:
        task = await a2a_registry.cancel_task(agent_name, task_id)
    except A2AError as e:
        raise HTTPException(502, str(e)) from e
    return task.model_dump()


# ---------- 状态 ----------

@router.get("/status", summary="获取 A2A 通信状态")
async def get_a2a_status():
    """内置 Agent + 已连接的外部 Agent 状态"""
    from app.agents import _AGENT_REGISTRY, _loaded_agents, _use_compiled, get_agent

    builtin = []
    if _use_compiled:
        for name in _loaded_agents:
            agent = _loaded_agents[name]
            builtin.append({"name": name, "display_name": agent.display_name, "type": "builtin"})
    else:
        for name in _AGENT_REGISTRY:
            try:
                agent = get_agent(name)
                builtin.append({"name": name, "display_name": agent.display_name, "type": "builtin"})
            except KeyError:
                continue

    external = a2a_registry.connected_agents

    return {
        "connected": len(builtin) > 0 or len(external) > 0,
        "total_registered": len(builtin) + len(external),
        "agents": builtin + external,
        "external_count": len(external),
    }
