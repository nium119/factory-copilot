"""Agent 注册表与意图路由配置"""
import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "agent.db")

# Agent 注册表 — 按需延迟加载
_AGENT_REGISTRY = {
    "general": "app.agents.general:general_agent",
    "scheduling": "app.agents.scheduling:scheduling_agent",
    "quality": "app.agents.quality:quality_agent",
    "equipment": "app.agents.equipment:equipment_agent",
    "inventory": "app.agents.inventory:inventory_agent",
    "process": "app.agents.process:process_agent",
    "production_prep": "app.agents.production_prep:production_prep_agent",
    "andon": "app.agents.andon:andon_agent",
    "workstation": "app.agents.workstation:workstation_agent",
}

_loaded_agents = {}


def _get_db():
    """获取数据库连接"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _load_agent_config(name):
    """从数据库加载单个 Agent 配置"""
    try:
        conn = _get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM agents WHERE name = ?", (name,))
        row = cursor.fetchone()
        conn.close()
        if row:
            return dict(row)
    except Exception:
        pass
    return None


def _load_all_agent_configs():
    """从数据库加载所有启用的 Agent 配置"""
    try:
        conn = _get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM agents WHERE enabled = 1 ORDER BY sort_order DESC")
        rows = [dict(r) for r in cursor.fetchall()]
        conn.close()
        return rows
    except Exception:
        return []


def _apply_db_config_to_agent(agent, config):
    """将数据库配置应用到 Agent 实例（仅展示层字段）"""
    if config:
        if config.get("display_name"):
            agent.display_name = config["display_name"]
        if config.get("icon"):
            agent.icon = config["icon"]
        if config.get("color"):
            agent.color = config["color"]
        if config.get("description"):
            agent.description = config["description"]
    return agent


def get_agent(name: str):
    """按需加载并返回 Agent 实例"""
    if name in _loaded_agents:
        return _loaded_agents[name]

    if name not in _AGENT_REGISTRY:
        from app.agents.general import general_agent
        return general_agent

    module_path, attr_name = _AGENT_REGISTRY[name].split(":")
    import importlib
    module = importlib.import_module(module_path)
    agent = getattr(module, attr_name)

    # 从数据库加载配置并覆盖
    config = _load_agent_config(name)
    if config:
        _apply_db_config_to_agent(agent, config)

    _loaded_agents[name] = agent
    return agent


def get_all_agents():
    """返回所有已注册的 Agent"""
    return {name: get_agent(name) for name in _AGENT_REGISTRY}


def get_agents_from_db():
    """从数据库获取 Agent 信息列表（给 API 用）"""
    configs = _load_all_agent_configs()
    result = []
    for cfg in configs:
        if cfg["name"] in _AGENT_REGISTRY:
            agent = get_agent(cfg["name"])
            info = agent.get_info()
            info["enabled"] = cfg.get("enabled", True)
            result.append(info)
        else:
            result.append({
                "name": cfg["name"],
                "display_name": cfg["display_name"],
                "icon": cfg["icon"],
                "color": cfg["color"],
                "description": cfg["description"],
                "enabled": cfg.get("enabled", True),
            })
    return result


