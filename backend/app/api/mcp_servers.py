"""MCP 服务器管理 API — Add/remove MCP servers at runtime without restart."""

import json
import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.mcp import mcp_registry

router = APIRouter(prefix="/mcp/servers", tags=["MCP管理"])

_DB_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "agent.db")


def _get_db():
    import sqlite3
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_table():
    conn = _get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS mcp_servers (
            name TEXT PRIMARY KEY,
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


class MCPServerIn(BaseModel):
    name: str
    command: str
    args: list[str] = []
    enabled: bool = True
    description: str = ""


class MCPServerOut(BaseModel):
    name: str
    command: str
    args: list[str]
    enabled: bool
    description: str
    connected: bool = False
    tool_count: int = 0
    created_at: str = ""
    updated_at: str = ""


def _row_to_out(row) -> MCPServerOut:
    r = dict(row)
    client = mcp_registry._clients.get(r["name"])
    return MCPServerOut(
        name=r["name"],
        command=r["command"],
        args=json.loads(r["args"]),
        enabled=bool(r["enabled"]),
        description=r.get("description", ""),
        connected=client.is_connected if client else False,
        tool_count=len(client.tools) if client and client.is_connected else 0,
        created_at=r.get("created_at", ""),
        updated_at=r.get("updated_at", ""),
    )


@router.get("", summary="列出所有 MCP 服务器")
def list_servers():
    _ensure_table()
    conn = _get_db()
    try:
        rows = conn.execute("SELECT * FROM mcp_servers ORDER BY name").fetchall()
        return [_row_to_out(r) for r in rows]
    finally:
        conn.close()


@router.post("", summary="新增 MCP 服务器")
async def create_server(srv: MCPServerIn):
    _ensure_table()
    conn = _get_db()
    try:
        existing = conn.execute("SELECT 1 FROM mcp_servers WHERE name=?", (srv.name,)).fetchone()
        if existing:
            raise HTTPException(409, f"MCP 服务器已存在: {srv.name}")
        conn.execute(
            "INSERT INTO mcp_servers (name, command, args, enabled, description) VALUES (?,?,?,?,?)",
            (srv.name, srv.command, json.dumps(srv.args), int(srv.enabled), srv.description),
        )
        conn.commit()
        if srv.enabled:
            try:
                await mcp_registry.connect_server(srv.name, srv.command, srv.args)
            except Exception as e:
                raise HTTPException(500, f"保存成功但连接失败: {e}")
        return {"ok": True, "name": srv.name}
    finally:
        conn.close()


@router.put("/{name}", summary="更新 MCP 服务器")
async def update_server(name: str, srv: MCPServerIn):
    _ensure_table()
    conn = _get_db()
    try:
        existing = conn.execute("SELECT * FROM mcp_servers WHERE name=?", (name,)).fetchone()
        if not existing:
            raise HTTPException(404, f"MCP 服务器不存在: {name}")
        conn.execute(
            "UPDATE mcp_servers SET command=?, args=?, enabled=?, description=?, updated_at=datetime('now','localtime') WHERE name=?",
            (srv.command, json.dumps(srv.args), int(srv.enabled), srv.description, name),
        )
        conn.commit()
        # 断开旧连接，重新连接
        if name in mcp_registry._clients:
            await mcp_registry._clients[name].close()
            del mcp_registry._clients[name]
        if srv.enabled:
            try:
                await mcp_registry.connect_server(srv.name, srv.command, srv.args)
            except Exception as e:
                raise HTTPException(500, f"更新成功但连接失败: {e}")
        return {"ok": True, "name": name}
    finally:
        conn.close()


@router.delete("/{name}", summary="删除 MCP 服务器")
async def delete_server(name: str):
    _ensure_table()
    conn = _get_db()
    try:
        existing = conn.execute("SELECT 1 FROM mcp_servers WHERE name=?", (name,)).fetchone()
        if not existing:
            raise HTTPException(404, f"MCP 服务器不存在: {name}")
        conn.execute("DELETE FROM mcp_servers WHERE name=?", (name,))
        conn.commit()
        if name in mcp_registry._clients:
            await mcp_registry._clients[name].close()
            del mcp_registry._clients[name]
        return {"ok": True, "name": name}
    finally:
        conn.close()


@router.post("/{name}/connect", summary="连接 MCP 服务器")
async def connect_server(name: str):
    _ensure_table()
    conn = _get_db()
    try:
        row = conn.execute("SELECT * FROM mcp_servers WHERE name=?", (name,)).fetchone()
        if not row:
            raise HTTPException(404, f"MCP 服务器不存在: {name}")
        args = json.loads(row["args"])
        await mcp_registry.connect_server(row["name"], row["command"], args)
        return {"ok": True, "name": name, "tool_count": len(mcp_registry._clients[name].tools)}
    finally:
        conn.close()


@router.post("/{name}/disconnect", summary="断开 MCP 服务器")
async def disconnect_server(name: str):
    if name not in mcp_registry._clients:
        raise HTTPException(404, f"MCP 服务器未连接: {name}")
    await mcp_registry._clients[name].close()
    del mcp_registry._clients[name]
    return {"ok": True, "name": name}
