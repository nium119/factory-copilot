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
    """启动时运行编译器, 注册编译产出的 Agent + 同步到 agent.db。

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

            # 同步到 agent.db: Agent 定义 + 链
            _sync_agents_to_db(runtime)
            _sync_chains_to_db(runtime)

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


def _sync_agents_to_db(runtime):
    """将编译器产出的 Agent 定义写入 agent.db。"""
    try:
        conn = _get_db()
        c = conn.cursor()
        for i, ad in enumerate(runtime.agents):
            c.execute(
                """INSERT OR REPLACE INTO agents
                   (name, display_name, icon, color, description, enabled, roles, keywords, system_prompt, sort_order)
                   VALUES (?, ?, ?, ?, ?, 1, '[]', '[]', ?, ?)""",
                (ad.name, ad.display_name, ad.icon, ad.color,
                 ad.description, ad.system_prompt, len(runtime.agents) - i),
            )
        # 禁用在编译器产出中不存在的旧 Agent
        compiled_names = {ad.name for ad in runtime.agents}
        c.execute("SELECT name FROM agents WHERE enabled=1")
        for row in c.fetchall():
            if row["name"] not in compiled_names:
                c.execute("UPDATE agents SET enabled=0 WHERE name=?", (row["name"],))
                logger.info(f"[Compiler] 禁用旧 Agent: {row['name']}")
        conn.commit()
        conn.close()
        logger.info(f"[Compiler] {len(runtime.agents)} Agent 定义已同步到 agent.db")
    except Exception as e:
        logger.warning(f"[Compiler] Agent DB 同步失败: {e}")


def _sync_chains_to_db(runtime):
    """将编译器产出的链定义写入 agent.db chains 表。"""
    import json
    if not runtime.chains:
        return
    try:
        conn = _get_db()
        c = conn.cursor()
        # 确保表存在
        c.execute("""CREATE TABLE IF NOT EXISTS chains (
            chain_id TEXT PRIMARY KEY, name TEXT, description TEXT,
            triggers TEXT, final_prompt_template TEXT, focus_concepts TEXT,
            enabled INTEGER DEFAULT 1, created_at TEXT, updated_at TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS chain_steps (
            id INTEGER PRIMARY KEY AUTOINCREMENT, chain_id TEXT,
            step_order INTEGER, step_id TEXT, description TEXT,
            agent_name TEXT, prompt_template TEXT, output_key TEXT,
            focus_concepts TEXT,
            FOREIGN KEY(chain_id) REFERENCES chains(chain_id))""")

        synced = 0
        for chain in runtime.chains[:20]:
            c.execute(
                """INSERT OR REPLACE INTO chains
                   (chain_id, name, description, triggers, final_prompt_template, focus_concepts, enabled)
                   VALUES (?, ?, ?, ?, ?, ?, 1)""",
                (
                    chain.name, chain.display_name, chain.description,
                    json.dumps(chain.triggers, ensure_ascii=False),
                    chain.steps[-1].get("prompt_template", "") if chain.steps else "",
                    ",".join(chain.path),
                ),
            )
            c.execute("DELETE FROM chain_steps WHERE chain_id=?", (chain.name,))
            for i, step in enumerate(chain.steps):
                c.execute(
                    """INSERT INTO chain_steps
                       (chain_id, step_order, step_id, description, agent_name, prompt_template, output_key, focus_concepts)
                       VALUES (?, ?, ?, ?, '', ?, ?, ?)""",
                    (chain.name, i, step.get("step_id", f"step_{i}"),
                     step.get("description", ""),
                     step.get("prompt_template", ""),
                     step.get("output_key", ""),
                     step.get("focus_concepts", "")),
                )
            synced += 1
        conn.commit()
        conn.close()
        logger.info(f"[Compiler] {synced} 链已同步到 agent.db")
    except Exception as e:
        logger.warning(f"[Compiler] Chain DB 同步失败: {e}")


def get_compiled_runtime():
    """获取最近一次编译器产出。"""
    return _compiled_runtime


