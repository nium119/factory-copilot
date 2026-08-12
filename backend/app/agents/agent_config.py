"""Agent 元数据配置中心 — 从 agent.db (ORM) 加载。"""
from typing import Any, Dict, List


async def _load_agents_async() -> Dict[str, Dict[str, Any]]:
    """从 agent.db 加载所有启用的 Agent 定义（ORM 版本）。"""
    from app.db import get_db
    from app.repositories.agent_repository import AgentRepository
    try:
        async for session in get_db():
            repo = AgentRepository(session)
            agents = await repo.get_enabled_agents()
            return {
                a.name: {
                    "name": a.name, "display_name": a.display_name,
                    "icon": a.icon, "color": a.color,
                    "description": a.description, "enabled": a.enabled,
                    "roles": a.roles or [], "keywords": a.keywords or [],
                    "system_prompt": a.system_prompt, "sort_order": a.sort_order,
                    "namespace": a.namespace or "",
                }
                for a in agents
            }
    except Exception:
        return {}


def _load_agents_from_db() -> Dict[str, Dict[str, Any]]:
    """从 agent.db 加载所有启用的 Agent 定义（同步包装）。"""
    from app.db import run_async
    try:
        return run_async(_load_agents_async())
    except Exception:
        return {}


AGENT_DEFINITIONS: Dict[str, Dict[str, Any]] = {}


def _init_definitions():
    global AGENT_DEFINITIONS
    if not AGENT_DEFINITIONS:
        AGENT_DEFINITIONS = _load_agents_from_db()


def _replace_definitions(new_defs: Dict[str, Dict[str, Any]]) -> None:
    """原地替换 AGENT_DEFINITIONS 内容，保持 dict 对象不变。

    必须原地更新（clear+update）而非重新赋值：router.py / chain_engine.py
    等模块在导入时用 `from ... import AGENT_DEFINITIONS` 绑定了原对象引用，
    重新赋值新对象后这些引用仍指向旧数据（业务域切换后路由仍走旧 Agent，
    导致「无可用 Agent」）。原地更新让所有绑定方立即看到最新定义。
    """
    AGENT_DEFINITIONS.clear()
    AGENT_DEFINITIONS.update(new_defs)


def reload():
    """重新从 DB 加载 Agent 定义（API 调用后刷新缓存）。"""
    _replace_definitions(_load_agents_from_db())


# 确保首次访问时已加载
_init_definitions()


def get_agent_metadata(name: str) -> Dict[str, str]:
    info = AGENT_DEFINITIONS.get(name, {})
    return {
        "name": name,
        "display_name": info.get("display_name", name),
        "icon": info.get("icon", "?"),
        "color": info.get("color", "#6c5ce7"),
        "description": info.get("description", ""),
    }


def get_all_agent_names(exclude: List[str] = None) -> List[str]:
    names = list(AGENT_DEFINITIONS.keys())
    if exclude:
        names = [n for n in names if n not in exclude]
    return names
