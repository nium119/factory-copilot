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
    """按需加载并返回 Agent 实例（仅编译模式）。"""
    if name in _loaded_agents:
        return _loaded_agents[name]

    # 编译模式下: 从注册表加载（兼容编译Agent引用的旧模块）
    if _use_compiled and name in _AGENT_REGISTRY:
        module_path, attr_name = _AGENT_REGISTRY[name].split(":")
        import importlib
        module = importlib.import_module(module_path)
        agent = getattr(module, attr_name)
        config = _load_agent_config(name)
        if config:
            _apply_db_config_to_agent(agent, config)
        _loaded_agents[name] = agent
        return agent

    raise KeyError(f"Agent '{name}' 未注册（编译模式={_use_compiled}）")


def get_agents_from_db():
    """从数据库获取 Agent 信息列表（给 API 用）。
    仅编译模式返回 Agent；无域配置时返回空列表。"""
    if not _use_compiled:
        return []
    configs = _load_all_agent_configs()
    result = []
    for cfg in configs:
        try:
            agent = get_agent(cfg["name"])
            info = agent.get_info()
            info["enabled"] = cfg.get("enabled", True)
            result.append(info)
        except KeyError:
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

def _migrate_yaml_to_db():
    """一次性将 YAML 配置迁移到 DB。"""
    import os, json, yaml as _yaml, sqlite3 as _sql
    config_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "config")
    db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "agent.db")
    if not os.path.exists(db_path):
        return
    conn = _sql.connect(db_path)
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS namespace_configs (
            namespace TEXT NOT NULL, config_type TEXT NOT NULL,
            config_data TEXT NOT NULL DEFAULT '{}', updated_at TEXT,
            PRIMARY KEY (namespace, config_type))""")
        for ns in ["manufacturing", "sample"]:
            for ct, filename in [("domains", f"config/{ns}_domains.yaml"), ("systems", f"config/{ns}_systems.yaml")]:
                path = os.path.join(config_dir, filename)
                if os.path.exists(path):
                    with open(path, encoding="utf-8") as f:
                        config = _yaml.safe_load(f) or {}
                    if config:
                        c = conn.cursor()
                        c.execute("SELECT 1 FROM namespace_configs WHERE namespace=? AND config_type=?", (ns, ct))
                        if not c.fetchone():
                            c.execute(
                                "INSERT INTO namespace_configs (namespace, config_type, config_data, updated_at) VALUES (?,?,?,datetime('now'))",
                                (ns, ct, json.dumps(config, ensure_ascii=False))
                            )
                            logger.info(f"[Migrate] YAML→DB: {ns}/{ct}")
        conn.commit()
    except Exception as e:
        logger.warning(f"[Migrate] 失败: {e}")
    finally:
        conn.close()


async def compile_and_register():
    """启动时运行编译器, 注册编译产出的 Agent + 同步到 agent.db。

    有域配置时走编译模式；无配置时无 Agent（侧边栏空）。
    """
    # 首次启动时迁移
    _migrate_yaml_to_db()
    global _compiled_runtime, _use_compiled

    try:
        from app.agents.compiler import OntologyCompiler
        from app.agents.generic import create_agents_from_runtime

        compiler = OntologyCompiler()
        runtime = await compiler.compile()

        if runtime.skills and runtime.agents:
            agents = create_agents_from_runtime(runtime)
            _loaded_agents.update(agents)
            _compiled_runtime = runtime
            _use_compiled = True

            # 同步到 agent.db: Agent 定义 + 链 + Skill 触发词
            _sync_agents_to_db(runtime)
            _sync_chains_to_db(runtime)
            _sync_skill_triggers_to_db(runtime)

            logger.info(
                f"[Compiler] 编译模式已激活: "
                f"{len(runtime.skills)} skills, {len(agents)} agents"
            )
            return runtime
        else:
            logger.warning("[Compiler] 编译无产出, 无可用 Agent（请配置业务域）")
    except Exception as e:
        logger.error(f"[Compiler] 编译失败: {e}")

    _use_compiled = False
    _loaded_agents.clear()
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
    """将编译器产出的链定义写入 agent.db chains 表。
    标记 source='compiler'，仅覆盖/禁用编译器链，手动链不受影响。
    编译器 chain 为空时仍会禁用旧的编译器链。"""
    import json
    try:
        conn = _get_db()
        c = conn.cursor()
        c.execute("""CREATE TABLE IF NOT EXISTS chains (
            chain_id TEXT PRIMARY KEY, name TEXT, description TEXT,
            triggers TEXT, final_prompt_template TEXT, focus_concepts TEXT,
            enabled INTEGER DEFAULT 1, created_at TEXT, updated_at TEXT)""")
        try:
            c.execute("ALTER TABLE chains ADD COLUMN source TEXT DEFAULT 'manual'")
        except:
            pass

        compiler_ids = [ch.name for ch in (runtime.chains or [])[:20]]
        synced = 0
        for chain in (runtime.chains or [])[:20]:
            c.execute(
                """INSERT OR REPLACE INTO chains
                   (chain_id, name, description, triggers, final_prompt_template, focus_concepts, enabled, source)
                   VALUES (?, ?, ?, ?, ?, ?, 1, 'compiler')""",
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
        # 禁用 source='compiler' 但不在本次产出中的旧链（含 compiler chain 为空时全禁）
        c.execute("UPDATE chains SET enabled=0 WHERE source='compiler' AND chain_id NOT IN ({})".format(
            ",".join("?" * len(compiler_ids)) if compiler_ids else "'__none__'"
        ), compiler_ids if compiler_ids else [])
        conn.commit()
        conn.close()
        if synced > 0:
            logger.info(f"[Compiler] {synced} 编译器链已同步（手动链未受影响）")
        else:
            logger.info("[Compiler] 无编译器链产出，旧编译器链已禁用")
    except Exception as e:
        logger.warning(f"[Compiler] Chain DB 同步失败: {e}")


def get_compiled_runtime():
    """获取最近一次编译器产出。"""
    return _compiled_runtime


def _sync_skill_triggers_to_db(runtime):
    """将编译器生成的触发词自动写入 skill_overrides。
    已有用户自定义的 Skill 不覆盖（保留 triggers 和 enabled 状态）。"""
    import json, os
    ns = ""
    try:
        ns_file = os.path.join(os.path.dirname(__file__), "..", "..", "config", "active_namespace.txt")
        if os.path.exists(ns_file):
            with open(ns_file, encoding="utf-8") as f:
                ns = f.read().strip()
    except Exception:
        pass
    ns = ns or "manufacturing"
    try:
        conn = _get_db()
        c = conn.cursor()
        c.execute("SELECT config_data FROM namespace_configs WHERE namespace=? AND config_type=?", (ns, "skill_overrides"))
        row = c.fetchone()
        existing = {}
        if row and row["config_data"]:
            existing = json.loads(row["config_data"])
        # 合并：只补新 Skill，已有的保留用户自定义
        for s in runtime.skills:
            if s.name not in existing:
                existing[s.name] = {"enabled": True, "triggers": list(s.triggers)}
        c.execute(
            "INSERT OR REPLACE INTO namespace_configs (namespace, config_type, config_data, updated_at) VALUES (?,?,?,datetime('now'))",
            (ns, "skill_overrides", json.dumps(existing, ensure_ascii=False)),
        )
        conn.commit()
        conn.close()
        if runtime.skills:
            logger.info(f"[Compiler] {len(runtime.skills)} Skill 触发词已同步到 DB")
    except Exception as e:
        logger.warning(f"[Compiler] Skill 触发词同步失败: {e}")


def get_disabled_skills() -> set:
    """从 DB skill_overrides 读取被禁用的 Skill 名列表。"""
    import sqlite3, json, os
    from app.core.logger import log
    ns = ""
    try:
        ns_file = os.path.join(os.path.dirname(__file__), "..", "..", "config", "active_namespace.txt")
        if os.path.exists(ns_file):
            with open(ns_file, encoding="utf-8") as f:
                ns = f.read().strip()
    except Exception:
        pass
    ns = ns or "manufacturing"
    db_path = os.path.join(os.path.dirname(__file__), "..", "..", "data", "agent.db")
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT config_data FROM namespace_configs WHERE namespace=? AND config_type=?", (ns, "skill_overrides"))
        row = c.fetchone()
        conn.close()
        if row and row["config_data"]:
            overrides = json.loads(row["config_data"])
            return {name for name, cfg in overrides.items() if isinstance(cfg, dict) and cfg.get("enabled") is False}
    except Exception:
        pass
    return set()


