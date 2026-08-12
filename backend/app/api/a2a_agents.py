"""A2A 外部 Agent 管理 API — 配置增删改查，运行时连接外部 Agent（HTTP A2A 协议）"""

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger import log
from app.db import get_db
from app.repositories.a2a_agent_repo import A2aAgentRepository

router = APIRouter(prefix="/a2a/agents", tags=["A2A管理"])


# ---------- Pydantic schemas ----------

class A2AAgentIn(BaseModel):
    name: str
    display_name: str = ""
    url: str
    enabled: bool = True
    description: str = ""
    auto_collab: bool = False


class A2AAgentOut(BaseModel):
    name: str
    display_name: str
    url: str
    enabled: bool
    description: str
    auto_collab: bool = False
    registered: bool = False  # 兼容旧字段，含义 = 已连接
    connected: bool = False
    agent_card: dict | None = None
    created_at: str = ""
    updated_at: str = ""


def _model_to_out(m) -> A2AAgentOut:
    from app.a2a.registry import a2a_registry
    client = a2a_registry.get_client(m.name)
    connected = bool(client and client.is_connected)
    card = client.agent_card.model_dump() if (client and client.agent_card) else None
    return A2AAgentOut(
        name=m.name,
        display_name=m.display_name or "",
        url=m.url or "",
        enabled=m.enabled,
        description=m.description or "",
        auto_collab=getattr(m, "auto_collab", False) or False,
        registered=connected,
        connected=connected,
        agent_card=card,
        created_at=m.created_at.isoformat() if m.created_at else "",
        updated_at=m.updated_at.isoformat() if m.updated_at else "",
    )


# ---------- runtime helpers ----------

async def _connect_background(name: str, url: str, auto_collab: bool):
    """后台连接外部 Agent（不阻塞请求）；失败仅告警"""
    from app.a2a.registry import a2a_registry
    try:
        await a2a_registry.connect_agent(name, url, auto_collab=auto_collab)
    except Exception as e:
        log.warning(f"[A2A] 连接失败 {name}: {e}")


def _register_runtime(name: str, url: str, auto_collab: bool):
    """非阻塞触发外部 Agent 连接（HTTP 握手获取 Agent Card）"""
    if not url or not url.strip():
        return
    asyncio.create_task(_connect_background(name, url.strip(), auto_collab))


def _unregister_runtime(name: str):
    from app.a2a.registry import a2a_registry
    asyncio.create_task(a2a_registry.close_agent(name))


# ---------- CRUD endpoints (ORM) ----------

@router.get("", summary="列出所有 A2A 外部 Agent")
async def list_agents(db: AsyncSession = Depends(get_db)):
    repo = A2aAgentRepository(db)
    agents = await repo.list_all()
    return [_model_to_out(a) for a in agents]


@router.post("", summary="新增 A2A 外部 Agent")
async def create_agent(agent: A2AAgentIn, db: AsyncSession = Depends(get_db)):
    repo = A2aAgentRepository(db)
    existing = await repo.get_by_name(agent.name)
    if existing:
        raise HTTPException(409, f"Agent 已存在: {agent.name}")
    await repo.create(
        name=agent.name,
        display_name=agent.display_name,
        url=agent.url.strip(),
        enabled=agent.enabled,
        description=agent.description,
        auto_collab=agent.auto_collab,
    )
    if agent.enabled:
        _register_runtime(agent.name, agent.url, agent.auto_collab)
    return {"ok": True, "name": agent.name}


@router.put("/{name}", summary="更新 A2A 外部 Agent")
async def update_agent(name: str, agent: A2AAgentIn, db: AsyncSession = Depends(get_db)):
    repo = A2aAgentRepository(db)
    existing = await repo.get_by_name(name)
    if not existing:
        raise HTTPException(404, f"Agent 不存在: {name}")
    await repo.update(
        name,
        display_name=agent.display_name,
        url=agent.url.strip(),
        enabled=agent.enabled,
        description=agent.description,
        auto_collab=agent.auto_collab,
    )
    # 断开旧连接后按新配置重连
    _unregister_runtime(name)
    if agent.enabled:
        _register_runtime(agent.name, agent.url, agent.auto_collab)
    return {"ok": True, "name": name}


@router.delete("/{name}", summary="删除 A2A 外部 Agent")
async def delete_agent(name: str, db: AsyncSession = Depends(get_db)):
    repo = A2aAgentRepository(db)
    existing = await repo.get_by_name(name)
    if not existing:
        raise HTTPException(404, f"Agent 不存在: {name}")
    await repo.delete(name)
    _unregister_runtime(name)
    return {"ok": True, "name": name}
