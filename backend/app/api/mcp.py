"""MCP 状态 API — 前端 MCP 工具状态指示器"""
from fastapi import APIRouter

router = APIRouter(prefix="/mcp", tags=["MCP"])


@router.get("/status", summary="获取 MCP 连接状态")
async def get_mcp_status():
    """返回所有已连接的 MCP Server 信息和工具列表"""
    from app.mcp import mcp_registry
    servers = []
    for s in mcp_registry.connected_servers:
        servers.append({
            "name": s["name"],
            "info": s["info"],
            "tool_count": s["tool_count"],
        })
    # 展开工具名
    from app.agents.settings import TOOL_SAFETY
    mcp_tools = {k: v for k, v in TOOL_SAFETY.items() if k.startswith("mcp_")}
    return {
        "connected": len(servers) > 0,
        "servers": servers,
        "tool_count": sum(s["tool_count"] for s in servers),
        "tools": [{"name": k, "risk": v.get("risk", "READ")} for k, v in mcp_tools.items()],
    }
