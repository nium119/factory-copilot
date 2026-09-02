"""MCP 服务器管理 API — Add/remove MCP servers at runtime without restart."""

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.mcp import mcp_registry
from app.repositories.mcp_server_repo import McpServerRepository
from app.api.chains import _load_config, _save_config

router = APIRouter(prefix="/mcp/servers", tags=["MCP管理"])


# ---------- Pydantic schemas ----------

class MCPServerIn(BaseModel):
    name: str
    url: str = ""                # HTTP(SSE) 远程地址；非空时优先于 command/args（免本地脚本）
    command: str = ""
    args: list[str] = []
    enabled: bool = True
    description: str = ""
    tool_risks: dict = Field(default_factory=dict)  # 工具风险声明 {tool_name: risk}


class MCPServerOut(BaseModel):
    name: str
    url: str = ""
    command: str
    args: list[str]
    enabled: bool
    description: str
    tool_risks: dict = Field(default_factory=dict)
    connected: bool = False
    tool_count: int = 0
    tools: list[dict] = []
    created_at: str = ""
    updated_at: str = ""


def _model_to_out(m) -> MCPServerOut:
    client = mcp_registry._clients.get(m.name)
    tool_risks = {}
    try:
        tool_risks = json.loads(m.tool_risks) if getattr(m, "tool_risks", "") else {}
    except Exception:
        tool_risks = {}
    tools = []
    if client and client.is_connected:
        from app.agents.settings import TOOL_SAFETY
        for tname, tool in client.tools.items():
            key = f"mcp_{m.name}_{tname}"
            tools.append({
                "name": tname,
                "description": tool.description or "",
                "input_schema": tool.input_schema or {},
                "risk": (TOOL_SAFETY.get(key) or {}).get("risk", tool_risks.get(tname, "READ")),
            })
    return MCPServerOut(
        name=m.name,
        url=getattr(m, "url", "") or "",
        command=m.command or "",
        args=json.loads(m.args) if m.args else [],
        enabled=m.enabled,
        description=m.description or "",
        tool_risks=tool_risks,
        connected=client.is_connected if client else False,
        tool_count=len(client.tools) if client and client.is_connected else 0,
        tools=tools,
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
    if not srv.url and not srv.command:
        raise HTTPException(400, "url（远程）与 command（stdio）至少填一个")
    repo = McpServerRepository(db)
    existing = await repo.get_by_name(srv.name)
    if existing:
        raise HTTPException(409, f"MCP 服务器已存在: {srv.name}")
    await repo.create(
        name=srv.name,
        url=srv.url,
        command=srv.command or "python",
        args=json.dumps(srv.args),
        enabled=srv.enabled,
        description=srv.description,
        tool_risks=json.dumps(srv.tool_risks or {}, ensure_ascii=False),
    )
    # 不自动连接，由「全部应用」统一处理
    return {"ok": True, "name": srv.name, "dirty": True}


@router.put("/overrides", summary="保存 MCP 工具覆盖配置")
async def save_mcp_overrides(data: dict):
    """MCP 工具跨 namespace，存在全局 _mcp 下。"""
    try:
        await _save_config("_mcp", "skill_overrides", data.get("overrides", {}))
        return {"ok": True, "message": "已保存"}
    except Exception as e:
        return {"ok": False, "message": str(e)}


@router.get("/overrides", summary="获取 MCP 工具覆盖配置")
async def get_mcp_overrides():
    return {"ok": True, "overrides": await _load_config("_mcp", "skill_overrides")}


@router.put("/{name}", summary="更新 MCP 服务器")
async def update_server(name: str, srv: MCPServerIn, db: AsyncSession = Depends(get_db)):
    if not srv.url and not srv.command:
        raise HTTPException(400, "url（远程）与 command（stdio）至少填一个")
    repo = McpServerRepository(db)
    existing = await repo.get_by_name(name)
    if not existing:
        raise HTTPException(404, f"MCP 服务器不存在: {name}")
    await repo.update(
        name,
        url=srv.url,
        command=srv.command or "python",
        args=json.dumps(srv.args),
        enabled=srv.enabled,
        description=srv.description,
        tool_risks=json.dumps(srv.tool_risks or {}, ensure_ascii=False),
    )
    # 不自动重连，由「全部应用」统一处理
    return {"ok": True, "name": name, "dirty": True}


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


async def _connect_row(row) -> None:
    """按配置连接一个 MCP server：url 非空走 HTTP(SSE) 远程传输，否则 stdio 子进程。"""
    args = json.loads(row.args) if row.args else []
    tool_risks = json.loads(row.tool_risks) if getattr(row, "tool_risks", "") else {}
    url = (getattr(row, "url", "") or "").strip()
    if url:
        await mcp_registry.connect_server(row.name, url=url, tool_risks=tool_risks)
    else:
        await mcp_registry.connect_server(row.name, row.command, args, tool_risks)


@router.post("/{name}/connect", summary="连接 MCP 服务器")
async def connect_server(name: str, db: AsyncSession = Depends(get_db)):
    repo = McpServerRepository(db)
    row = await repo.get_by_name(name)
    if not row:
        raise HTTPException(404, f"MCP 服务器不存在: {name}")
    await _connect_row(row)
    return {"ok": True, "name": name, "tool_count": len(mcp_registry._clients[name].tools)}


@router.post("/{name}/disconnect", summary="断开 MCP 服务器")
async def disconnect_server(name: str):
    if name not in mcp_registry._clients:
        raise HTTPException(404, f"MCP 服务器未连接: {name}")
    await mcp_registry._clients[name].close()
    del mcp_registry._clients[name]
    return {"ok": True, "name": name}


class McpCallRequest(BaseModel):
    tool: str = Field(..., description="工具名：完整名（mcp_ontology_search_concepts）或短名（search_concepts 自动补前缀）")
    arguments: dict = Field(default_factory=dict, description="工具入参")


@router.post("/{name}/call", summary="试调 MCP 工具（验证链路是否可用）")
async def call_mcp_tool(name: str, body: McpCallRequest):
    """经 FC 的 MCP 注册表试调一个工具 — 全链路验证 MCP Server 是否有用。

    链路：本端点 → mcp_registry（FC 进程内）→ MCP 客户端（HTTP/SSE 或 stdio）
    → MCP Server（如 OntoStudio）→ 实际执行。
    """
    client = mcp_registry._clients.get(name)
    if not client or not client.is_connected:
        raise HTTPException(404, f"MCP 服务器未连接: {name}（请先 connect）")

    # client.tools 的 key 是 MCP Server 端原始工具名（无 mcp_{name}_ 前缀）；
    # 调用方传完整名时去前缀，传短名时直接用。
    raw = body.tool[len(f"mcp_{name}_"):] if body.tool.startswith(f"mcp_{name}_") else body.tool
    if raw not in (client.tools or {}):
        available = ", ".join(list(client.tools)[:30])
        raise HTTPException(404, f"工具不存在: {raw}（可用: {available}）")

    try:
        result = await client.call_tool(raw, body.arguments or {})
        return {"ok": True, "server": name, "tool": raw, "result": result}
    except Exception as e:
        return {"ok": False, "server": name, "tool": raw, "error": str(e)}


@router.post("/apply", summary="应用所有 MCP 配置")
async def apply_mcp_servers(db: AsyncSession = Depends(get_db)):
    """连接所有启用的 MCP 服务器，重建 action_executor 和 router 索引。"""
    repo = McpServerRepository(db)
    servers = await repo.list_all()
    connected = 0
    failed = []
    for s in servers:
        if not s.enabled:
            continue
        try:
            await _connect_row(s)
            connected += 1
        except Exception as e:
            failed.append(f"{s.name}: {e}")
    # 重建路由索引
    from app.services.action_executor import action_executor
    from app.services.ontology_service import ontology_service
    from app.services.intent_router import intent_router
    action_executor.invalidate_cache()
    action_executor._ensure_loaded()
    intent_router.rebuild(ontology_service, action_executor)
    return {"ok": True, "connected": connected, "failed": failed, "total": len(servers)}


@router.post("/undo", summary="撤销 MCP 配置")
async def undo_mcp_servers():
    """断开所有 MCP 连接。"""
    await mcp_registry.close_all()
    from app.services.action_executor import action_executor
    from app.services.ontology_service import ontology_service
    from app.services.intent_router import intent_router
    action_executor.invalidate_cache()
    action_executor._ensure_loaded()
    intent_router.rebuild(ontology_service, action_executor)
    return {"ok": True, "message": "已断开所有 MCP 连接"}
