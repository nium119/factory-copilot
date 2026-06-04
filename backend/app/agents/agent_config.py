"""Agent 元数据配置中心 — 从 agent.db 加载，YAML 仅做种子数据"""
import os
import sqlite3
from typing import Any, Dict, List

_DB_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "agent.db")


def _load_agents_from_db() -> Dict[str, Dict[str, Any]]:
    """从 agent.db 加载所有启用的 Agent 定义。"""
    if not os.path.exists(_DB_PATH):
        return {}
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM agents WHERE enabled = 1 ORDER BY sort_order DESC")
        return {r["name"]: dict(r) for r in cursor.fetchall()}
    except Exception:
        return {}
    finally:
        conn.close()


AGENT_DEFINITIONS: Dict[str, Dict[str, Any]] = _load_agents_from_db()


def reload():
    """重新从 DB 加载 Agent 定义（API 调用后刷新缓存）。"""
    global AGENT_DEFINITIONS
    AGENT_DEFINITIONS = _load_agents_from_db()


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
