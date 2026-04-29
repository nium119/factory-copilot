"""A2A 状态 API — Agent 通信总线状态"""
from fastapi import APIRouter

router = APIRouter(prefix="/a2a", tags=["A2A"])


@router.get("/status", summary="获取 A2A 通信总线状态")
async def get_a2a_status():
    """返回所有已注册的 Agent 信息"""
    from app.agents import _AGENT_REGISTRY, get_agent
    from app.agents.external_agents import list_external, list_all

    builtin = []
    for name in _AGENT_REGISTRY:
        agent = get_agent(name)
        builtin.append({"name": name, "display_name": agent.display_name, "type": "builtin"})

    external = list_external()
    ext = [{"name": a, "display_name": a, "type": "external"} for a in external]

    return {
        "connected": len(builtin) > 0,
        "total_registered": len(builtin) + len(external),
        "agents": builtin + ext,
        "external_count": len(external),
    }
