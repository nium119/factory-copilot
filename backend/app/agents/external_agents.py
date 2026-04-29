"""外部 Agent 注册表 — 预留 MCP 对接"""
from typing import Dict, Callable, Awaitable, List
from app.core.logger import log

_registry: Dict[str, dict] = {}  # name → {handler, type, config}


def register(name: str, handler: Callable, agent_type: str = "external", config: dict = None) -> None:
    _registry[name] = {"handler": handler, "type": agent_type, "config": config or {}}
    log.info(f"[A2A] 注册: {name} (type={agent_type})")


def unregister(name: str) -> None:
    _registry.pop(name, None)


def list_external() -> List[str]:
    return [name for name, info in _registry.items() if info["type"] == "external"]


def list_all() -> List[dict]:
    return [{"name": name, "type": info["type"], **info["config"]} for name, info in _registry.items()]
