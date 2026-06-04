"""Agent 管理 API — agent.db 中 agents 表的增删改查。"""

import json
import os
import sqlite3

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.agents.agent_config import reload as reload_agents

router = APIRouter(prefix="/agents", tags=["Agent管理"])

_DB_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "agent.db")


def _get_conn():
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


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
    created_at: str = ""
    updated_at: str = ""


# ── 路由 ──────────────────────────────────────────────────────────

@router.get("", summary="获取所有Agent")
def list_agents():
    conn = _get_conn()
    try:
        c = conn.cursor()
        c.execute("SELECT * FROM agents ORDER BY sort_order DESC")
        agents = []
        for row in c.fetchall():
            r = dict(row)
            agents.append(AgentOut(
                name=r["name"],
                display_name=r.get("display_name", ""),
                icon=r.get("icon", ""),
                color=r.get("color", "#6c5ce7"),
                description=r.get("description", ""),
                enabled=bool(r.get("enabled", 1)),
                roles=json.loads(r.get("roles", "[]")),
                keywords=json.loads(r.get("keywords", "[]")),
                system_prompt=r.get("system_prompt") or "",
                sort_order=r.get("sort_order", 0),
                created_at=r.get("created_at", ""),
                updated_at=r.get("updated_at", ""),
            ))
        return agents
    finally:
        conn.close()


@router.get("/{name}", summary="获取单个Agent")
def get_agent(name: str):
    conn = _get_conn()
    try:
        c = conn.cursor()
        c.execute("SELECT * FROM agents WHERE name=?", (name,))
        row = c.fetchone()
        if not row:
            raise HTTPException(404, f"Agent不存在: {name}")
        r = dict(row)
        return AgentOut(
            name=r["name"],
            display_name=r.get("display_name", ""),
            icon=r.get("icon", ""),
            color=r.get("color", "#6c5ce7"),
            description=r.get("description", ""),
            enabled=bool(r.get("enabled", 1)),
            roles=json.loads(r.get("roles", "[]")),
            keywords=json.loads(r.get("keywords", "[]")),
            system_prompt=r.get("system_prompt", ""),
            sort_order=r.get("sort_order", 0),
            created_at=r.get("created_at", ""),
            updated_at=r.get("updated_at", ""),
        )
    finally:
        conn.close()


@router.post("", summary="创建Agent")
def create_agent(agent: AgentIn):
    conn = _get_conn()
    try:
        c = conn.cursor()
        c.execute("SELECT 1 FROM agents WHERE name=?", (agent.name,))
        if c.fetchone():
            raise HTTPException(409, f"Agent已存在: {agent.name}")

        c.execute(
            "INSERT INTO agents (name, display_name, icon, color, description, enabled, roles, keywords, system_prompt, sort_order) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                agent.name, agent.display_name, agent.icon, agent.color,
                agent.description, int(agent.enabled),
                json.dumps(agent.roles, ensure_ascii=False),
                json.dumps(agent.keywords, ensure_ascii=False),
                agent.system_prompt, agent.sort_order,
            ),
        )
        conn.commit()
        reload_agents()
        return {"ok": True, "name": agent.name}
    finally:
        conn.close()


@router.put("/{name}", summary="更新Agent")
def update_agent(name: str, agent: AgentIn):
    conn = _get_conn()
    try:
        c = conn.cursor()
        c.execute("SELECT 1 FROM agents WHERE name=?", (name,))
        if not c.fetchone():
            raise HTTPException(404, f"Agent不存在: {name}")

        c.execute(
            "UPDATE agents SET display_name=?, icon=?, color=?, description=?, "
            "enabled=?, roles=?, keywords=?, system_prompt=?, sort_order=?, "
            "updated_at=CURRENT_TIMESTAMP WHERE name=?",
            (
                agent.display_name, agent.icon, agent.color, agent.description,
                int(agent.enabled),
                json.dumps(agent.roles, ensure_ascii=False),
                json.dumps(agent.keywords, ensure_ascii=False),
                agent.system_prompt, agent.sort_order, name,
            ),
        )
        conn.commit()
        reload_agents()
        return {"ok": True, "name": name}
    finally:
        conn.close()


@router.delete("/{name}", summary="删除Agent")
def delete_agent(name: str):
    conn = _get_conn()
    try:
        c = conn.cursor()
        c.execute("SELECT 1 FROM agents WHERE name=?", (name,))
        if not c.fetchone():
            raise HTTPException(404, f"Agent不存在: {name}")
        c.execute("DELETE FROM agents WHERE name=?", (name,))
        conn.commit()
        reload_agents()
        return {"ok": True, "name": name}
    finally:
        conn.close()
