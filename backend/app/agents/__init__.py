"""Agent 注册表与意图路由配置"""
import os
import sqlite3

from loguru import logger

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "agent.db")

# Agent 注册表 — 按需延迟加载
_AGENT_REGISTRY = {
    "production_execution": "app.agents.production_execution:production_execution_agent",
    "production_management": "app.agents.production_management:production_management_agent",
    "quality_equipment": "app.agents.quality_equipment:quality_equipment_agent",
    "analysis_monitor": "app.agents.analysis_monitor:analysis_monitor_agent",
    "general": "app.agents.general:general_agent",
}

_loaded_agents = {}
_compiled_runtime = None  # 编译器产出
_use_compiled = False     # 是否使用编译模式


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
    except Exception as e:
        from app.core.logger import log
        log.warning(f"Failed to load agent config for '{name}': {e}")
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
    except Exception as e:
        from app.core.logger import log
        log.warning(f"Failed to load agent configs: {e}")
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
        from app.agents.analysis_monitor import analysis_monitor_agent
        return analysis_monitor_agent

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


def get_agents_from_db():
    """从数据库获取 Agent 信息列表（给 API 用）"""
    configs = _load_all_agent_configs()
    result = []
    for cfg in configs:
        if cfg["name"] in _AGENT_REGISTRY or _use_compiled:
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


# ── 编译器集成 ────────────────────────────────────────────

async def compile_and_register():
    """启动时运行编译器, 注册编译产出的 Agent。

    编译器可用时走编译模式, 否则回退到旧注册表。
    """
    global _compiled_runtime, _use_compiled

    try:
        from app.agents.compiler import OntologyCompiler
        from app.agents.generic import create_agents_from_runtime

        compiler = OntologyCompiler()
        runtime = await compiler.compile()

        if runtime.skills:
            agents = create_agents_from_runtime(runtime)
            _loaded_agents.update(agents)
            _compiled_runtime = runtime
            _use_compiled = True
            logger.info(
                f"[Compiler] 编译模式已激活: "
                f"{len(runtime.skills)} skills, {len(agents)} agents"
            )
            return runtime
        else:
            logger.warning("[Compiler] 编译无产出, 回退到旧 Agent 注册表")
    except Exception as e:
        logger.warning(f"[Compiler] 编译失败, 回退到旧 Agent 注册表: {e}")

    _use_compiled = False
    return None


def get_compiled_runtime():
    """获取最近一次编译器产出。"""
    return _compiled_runtime


