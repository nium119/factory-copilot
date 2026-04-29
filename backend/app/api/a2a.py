"""A2A 状态 API — Agent 通信总线状态"""
from fastapi import APIRouter

router = APIRouter(prefix="/a2a", tags=["A2A"])


@router.get("/status", summary="获取 A2A 通信总线状态")
async def get_a2a_status():
    """返回所有已注册的 Agent 信息"""
    from app.agents.a2a_bus import a2a_bus
    from app.agents import _AGENT_REGISTRY, get_agent

    registered = list(a2a_bus._agents.keys())
    external = [name for name in registered if name not in _AGENT_REGISTRY]

    builtin = []
    for name in registered:
        if name in _AGENT_REGISTRY:
            agent = get_agent(name)
            builtin.append({"name": name, "display_name": agent.display_name, "type": "builtin"})

    ext = [{"name": a, "display_name": a, "type": "external"} for a in external]

    return {
        "connected": len(registered) > 0,
        "total_registered": len(registered),
        "agents": builtin + ext,
        "external_count": len(external),
    }
