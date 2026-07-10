"""Agent 管理 API — 使用 ORM Repository。"""

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.agents.agent_config import reload as reload_agents
from app.repositories.agent_repository import AgentRepository

router = APIRouter(prefix="/agents", tags=["Agent管理"])


# ── Pydantic 模型 ─────────────────────────────────────────────────

class AgentIn(BaseModel):
    name: str
    display_name: str = ""
    icon: str = ""
    color: str = "#6c5ce7"
    description: str = ""
    enabled: bool = True
    roles: list[str] = []
    keywords: list[str] = []
    system_prompt: str = ""
    sort_order: int = 0


class AgentOut(BaseModel):
    name: str
    display_name: str
    icon: str
    color: str
    description: str
    enabled: bool
    roles: list[str]
    keywords: list[str]
    system_prompt: str
    sort_order: int
    project_description: str = ""
    created_at: str = ""
    updated_at: str = ""


# ── 路由 ──────────────────────────────────────────────────────────

@router.get("", summary="获取所有Agent")
async def list_agents(db: AsyncSession = Depends(get_db)):
    repo = AgentRepository(db)
    agents = await repo.get_all()
    return [
        AgentOut(
            name=a.name, display_name=a.display_name or "",
            icon=a.icon or "", color=a.color or "#6c5ce7",
            description=a.description or "", enabled=bool(a.enabled),
            roles=json.loads(a.roles) if isinstance(a.roles, str) else (a.roles or []),
            keywords=json.loads(a.keywords) if isinstance(a.keywords, str) else (a.keywords or []),
            system_prompt=a.system_prompt or "", project_description=a.project_description or "", sort_order=a.sort_order or 0,
            created_at=str(a.created_at) if a.created_at else "",
            updated_at=str(a.updated_at) if a.updated_at else "",
        ) for a in agents
    ]


@router.get("/{name}", summary="获取单个Agent")
async def get_agent(name: str, db: AsyncSession = Depends(get_db)):
    repo = AgentRepository(db)
    a = await repo.get_by_name(name)
    if not a:
        raise HTTPException(404, f"Agent不存在: {name}")
    return AgentOut(
        name=a.name, display_name=a.display_name or "",
        icon=a.icon or "", color=a.color or "#6c5ce7",
        description=a.description or "", enabled=bool(a.enabled),
        roles=json.loads(a.roles) if isinstance(a.roles, str) else (a.roles or []),
        keywords=json.loads(a.keywords) if isinstance(a.keywords, str) else (a.keywords or []),
        system_prompt=a.system_prompt or "", project_description=a.project_description or "", sort_order=a.sort_order or 0,
        created_at=str(a.created_at) if a.created_at else "",
        updated_at=str(a.updated_at) if a.updated_at else "",
    )


@router.post("", summary="创建Agent")
async def create_agent(agent: AgentIn, db: AsyncSession = Depends(get_db)):
    repo = AgentRepository(db)
    existing = await repo.get_by_name(agent.name)
    if existing:
        raise HTTPException(409, f"Agent已存在: {agent.name}")
    await repo.create(
        name=agent.name, display_name=agent.display_name, icon=agent.icon,
        color=agent.color, description=agent.description, enabled=agent.enabled,
        roles=agent.roles, keywords=agent.keywords, system_prompt=agent.system_prompt,
        sort_order=agent.sort_order,
    )
    reload_agents()
    return {"ok": True, "name": agent.name}


@router.put("/{name}", summary="更新Agent")
async def update_agent(name: str, agent: AgentIn, db: AsyncSession = Depends(get_db)):
    repo = AgentRepository(db)
    a = await repo.update(
        name, display_name=agent.display_name, icon=agent.icon,
        color=agent.color, description=agent.description, enabled=agent.enabled,
        roles=agent.roles, keywords=agent.keywords, system_prompt=agent.system_prompt,
        sort_order=agent.sort_order,
    )
    if not a:
        raise HTTPException(404, f"Agent不存在: {name}")
    reload_agents()
    return {"ok": True, "name": name}


@router.delete("/{name}", summary="删除Agent")
async def delete_agent(name: str, db: AsyncSession = Depends(get_db)):
    repo = AgentRepository(db)
    ok = await repo.delete(name)
    if not ok:
        raise HTTPException(404, f"Agent不存在: {name}")
    reload_agents()
    return {"ok": True, "name": name}
