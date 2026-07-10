"""A2A 外部 Agent 管理 API — 运行时添加/移除外部 Agent，无需重启."""

import json
import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.repositories.a2a_agent_repo import A2aAgentRepository

router = APIRouter(prefix="/a2a/agents", tags=["A2A管理"])

_DB_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "agent.db")


# ---------- raw sqlite3 helpers (保留给内部使用) ----------

def _get_db():
    import sqlite3
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_table():
    conn = _get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS a2a_agents (
            name TEXT PRIMARY KEY,
            display_name TEXT NOT NULL DEFAULT '',
            command TEXT NOT NULL,
            args TEXT NOT NULL DEFAULT '[]',
            enabled INTEGER NOT NULL DEFAULT 1,
            description TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.commit()
    conn.close()


# ---------- Pydantic schemas ----------

class A2AAgentIn(BaseModel):
    name: str
    display_name: str = ""
    command: str
    args: list[str] = []
    enabled: bool = True
    description: str = ""


class A2AAgentOut(BaseModel):
    name: str
    display_name: str
    command: str
    args: list[str]
    enabled: bool
    description: str
    registered: bool = False
    created_at: str = ""
    updated_at: str = ""


def _model_to_out(m) -> A2AAgentOut:
    from app.agents.external_agents import _registry
    return A2AAgentOut(
        name=m.name,
        display_name=m.display_name or "",
        command=m.command,
        args=json.loads(m.args) if m.args else [],
        enabled=m.enabled,
        description=m.description or "",
        registered=m.name in _registry,
        created_at=m.created_at.isoformat() if m.created_at else "",
        updated_at=m.updated_at.isoformat() if m.updated_at else "",
    )


# ---------- runtime helpers ----------

def _register_runtime(name: str, display_name: str, command: str, args: list):
    from app.agents.external_agents import register
    register(name, None, "external", {
        "display_name": display_name,
        "command": command,
        "args": args,
    })


def _unregister_runtime(name: str):
    from app.agents.external_agents import unregister
    unregister(name)


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
        command=agent.command,
        args=json.dumps(agent.args),
        enabled=agent.enabled,
        description=agent.description,
    )
    if agent.enabled:
        _register_runtime(agent.name, agent.display_name, agent.command, agent.args)
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
        command=agent.command,
        args=json.dumps(agent.args),
        enabled=agent.enabled,
        description=agent.description,
    )
    # 重新注册
    _unregister_runtime(name)
    if agent.enabled:
        _register_runtime(agent.name, agent.display_name, agent.command, agent.args)
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
