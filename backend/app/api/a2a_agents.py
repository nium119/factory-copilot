"""A2A 外部 Agent 管理 API — 运行时添加/移除外部 Agent，无需重启."""

import json
import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/a2a/agents", tags=["A2A管理"])

_DB_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "agent.db")


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


@router.get("", summary="列出所有 A2A 外部 Agent")
def list_agents():
    _ensure_table()
    conn = _get_db()
    try:
        from app.agents.external_agents import _registry
        rows = conn.execute("SELECT * FROM a2a_agents ORDER BY name").fetchall()
        result = []
        for row in rows:
            r = dict(row)
            result.append(A2AAgentOut(
                name=r["name"],
                display_name=r.get("display_name", ""),
                command=r["command"],
                args=json.loads(r["args"]),
                enabled=bool(r["enabled"]),
                description=r.get("description", ""),
                registered=r["name"] in _registry,
                created_at=r.get("created_at", ""),
                updated_at=r.get("updated_at", ""),
            ))
        return result
    finally:
        conn.close()


@router.post("", summary="新增 A2A 外部 Agent")
def create_agent(agent: A2AAgentIn):
    _ensure_table()
    conn = _get_db()
    try:
        existing = conn.execute("SELECT 1 FROM a2a_agents WHERE name=?", (agent.name,)).fetchone()
        if existing:
            raise HTTPException(409, f"Agent 已存在: {agent.name}")
        conn.execute(
            "INSERT INTO a2a_agents (name, display_name, command, args, enabled, description) VALUES (?,?,?,?,?,?)",
            (agent.name, agent.display_name, agent.command, json.dumps(agent.args), int(agent.enabled), agent.description),
        )
        conn.commit()
        if agent.enabled:
            _register_runtime(agent.name, agent.display_name, agent.command, agent.args)
        return {"ok": True, "name": agent.name}
    finally:
        conn.close()


@router.put("/{name}", summary="更新 A2A 外部 Agent")
def update_agent(name: str, agent: A2AAgentIn):
    _ensure_table()
    conn = _get_db()
    try:
        existing = conn.execute("SELECT * FROM a2a_agents WHERE name=?", (name,)).fetchone()
        if not existing:
            raise HTTPException(404, f"Agent 不存在: {name}")
        conn.execute(
            "UPDATE a2a_agents SET display_name=?, command=?, args=?, enabled=?, description=?, updated_at=datetime('now','localtime') WHERE name=?",
            (agent.display_name, agent.command, json.dumps(agent.args), int(agent.enabled), agent.description, name),
        )
        conn.commit()
        # 重新注册
        _unregister_runtime(name)
        if agent.enabled:
            _register_runtime(agent.name, agent.display_name, agent.command, agent.args)
        return {"ok": True, "name": name}
    finally:
        conn.close()


@router.delete("/{name}", summary="删除 A2A 外部 Agent")
def delete_agent(name: str):
    _ensure_table()
    conn = _get_db()
    try:
        existing = conn.execute("SELECT 1 FROM a2a_agents WHERE name=?", (name,)).fetchone()
        if not existing:
            raise HTTPException(404, f"Agent 不存在: {name}")
        conn.execute("DELETE FROM a2a_agents WHERE name=?", (name,))
        conn.commit()
        _unregister_runtime(name)
        return {"ok": True, "name": name}
    finally:
        conn.close()


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
