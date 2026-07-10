"""MCP 服务器管理 API — Add/remove MCP servers at runtime without restart."""

import json
import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.mcp import mcp_registry
from app.repositories.mcp_server_repo import McpServerRepository

router = APIRouter(prefix="/mcp/servers", tags=["MCP管理"])

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


# ---------- Pydantic schemas ----------

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


def _model_to_out(m) -> MCPServerOut:
    client = mcp_registry._clients.get(m.name)
    return MCPServerOut(
        name=m.name,
        command=m.command,
        args=json.loads(m.args) if m.args else [],
        enabled=m.enabled,
        description=m.description or "",
        connected=client.is_connected if client else False,
        tool_count=len(client.tools) if client and client.is_connected else 0,
        created_at=m.created_at.isoformat() if m.created_at else "",
        updated_at=m.updated_at.isoformat() if m.updated_at else "",
    )


# ---------- CRUD endpoints (ORM) ----------

@router.get("", summary="列出所有 MCP 服务器")
async def list_servers(db: AsyncSession = Depends(get_db)):
    repo = McpServerRepository(db)
    servers = await repo.list_all()
    return [_model_to_out(s) for s in servers]


@router.post("", summary="新增 MCP 服务器")
async def create_server(srv: MCPServerIn, db: AsyncSession = Depends(get_db)):
    repo = McpServerRepository(db)
    existing = await repo.get_by_name(srv.name)
    if existing:
        raise HTTPException(409, f"MCP 服务器已存在: {srv.name}")
    await repo.create(
        name=srv.name,
        command=srv.command,
        args=json.dumps(srv.args),
        enabled=srv.enabled,
        description=srv.description,
    )
    if srv.enabled:
        try:
            await mcp_registry.connect_server(srv.name, srv.command, srv.args)
        except Exception as e:
            raise HTTPException(500, f"保存成功但连接失败: {e}")
    return {"ok": True, "name": srv.name}


@router.put("/{name}", summary="更新 MCP 服务器")
async def update_server(name: str, srv: MCPServerIn, db: AsyncSession = Depends(get_db)):
    repo = McpServerRepository(db)
    existing = await repo.get_by_name(name)
    if not existing:
        raise HTTPException(404, f"MCP 服务器不存在: {name}")
    await repo.update(
        name,
        command=srv.command,
        args=json.dumps(srv.args),
        enabled=srv.enabled,
        description=srv.description,
    )
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


@router.delete("/{name}", summary="删除 MCP 服务器")
async def delete_server(name: str, db: AsyncSession = Depends(get_db)):
    repo = McpServerRepository(db)
    existing = await repo.get_by_name(name)
    if not existing:
        raise HTTPException(404, f"MCP 服务器不存在: {name}")
    await repo.delete(name)
    if name in mcp_registry._clients:
        await mcp_registry._clients[name].close()
        del mcp_registry._clients[name]
    return {"ok": True, "name": name}


@router.post("/{name}/connect", summary="连接 MCP 服务器")
async def connect_server(name: str, db: AsyncSession = Depends(get_db)):
    repo = McpServerRepository(db)
    row = await repo.get_by_name(name)
    if not row:
        raise HTTPException(404, f"MCP 服务器不存在: {name}")
    args = json.loads(row.args) if row.args else []
    await mcp_registry.connect_server(row.name, row.command, args)
    return {"ok": True, "name": name, "tool_count": len(mcp_registry._clients[name].tools)}


@router.post("/{name}/disconnect", summary="断开 MCP 服务器")
async def disconnect_server(name: str):
    if name not in mcp_registry._clients:
        raise HTTPException(404, f"MCP 服务器未连接: {name}")
    await mcp_registry._clients[name].close()
    del mcp_registry._clients[name]
    return {"ok": True, "name": name}
