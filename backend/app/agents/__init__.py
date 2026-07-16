"""Agent 注册表与意图路由配置"""
import os

from loguru import logger

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


def _load_agent_config(name):
    """从数据库加载单个 Agent 配置"""
    async def _load():
        from app.db import get_db
        async for session in get_db():
            from app.repositories.agent_repository import AgentRepository
            repo = AgentRepository(session)
            agent = await repo.get_by_name(name)
            if agent:
                return {
                    "name": agent.name,
                    "display_name": agent.display_name,
                    "icon": agent.icon,
                    "color": agent.color,
                    "description": agent.description,
                    "system_prompt": agent.system_prompt,
                    "sort_order": agent.sort_order,
                    "enabled": agent.enabled,
                    "roles": agent.roles,
                    "keywords": agent.keywords,
                }
        return None
    from app.db import run_async
    try:
        return run_async(_load())
    except Exception:
        return None


def _load_all_agent_configs():
    """从数据库加载所有启用的 Agent 配置"""
    async def _load():
        from app.db import get_db
        async for session in get_db():
            from app.repositories.agent_repository import AgentRepository
            repo = AgentRepository(session)
            agents = await repo.get_enabled_agents()
            result = []
            for agent in agents:
                result.append({
                    "name": agent.name,
                    "display_name": agent.display_name,
                    "icon": agent.icon,
                    "color": agent.color,
                    "description": agent.description,
                    "system_prompt": agent.system_prompt,
                    "sort_order": agent.sort_order,
                    "enabled": agent.enabled,
                    "roles": agent.roles,
                    "keywords": agent.keywords,
                })
            return result
        return []
    from app.db import run_async
    try:
        return run_async(_load())
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

async def _migrate_yaml_to_db():
    """一次性将 YAML 配置迁移到 DB。"""
    import os, json, yaml as _yaml
    config_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "config")
    db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "agent.db")
    if not os.path.exists(db_path):
        return
    try:
        from app.db import get_db
        async for session in get_db():
            from app.repositories.namespace_config_repo import NamespaceConfigRepository
            repo = NamespaceConfigRepository(session)
            for ns in ["manufacturing", "sample"]:
                for ct, filename in [("domains", f"config/{ns}_domains.yaml"), ("systems", f"config/{ns}_systems.yaml")]:
                    path = os.path.join(config_dir, filename)
                    if os.path.exists(path):
                        with open(path, encoding="utf-8") as f:
                            config = _yaml.safe_load(f) or {}
                        if config:
                            existing = await repo.get(ns, ct)
                            if not existing:
                                await repo.save(ns, ct, config)
                                logger.info(f"[Migrate] YAML→DB: {ns}/{ct}")
    except Exception as e:
        logger.warning(f"[Migrate] 失败: {e}")


async def compile_and_register():
    """启动时运行编译器, 注册编译产出的 Agent + 同步到 agent.db。

    有域配置时走编译模式；无配置时无 Agent（侧边栏空）。
    """
    # 首次启动时迁移
    await _migrate_yaml_to_db()
    global _compiled_runtime, _use_compiled

    try:
        from app.agents.compiler import OntologyCompiler
        from app.agents.generic import create_agents_from_runtime

        compiler = OntologyCompiler()
        runtime = await compiler.compile()

        if runtime.agents:
            agents = create_agents_from_runtime(runtime)
            _loaded_agents.update(agents)
            _compiled_runtime = runtime
            _use_compiled = True

            # 同步到 agent.db: Agent 定义 + 链 + Skill 触发词 + Embedding
            await _sync_agents_to_db(runtime)
            await _sync_chains_to_db(runtime)
            await _sync_skill_triggers_to_db(runtime)
            await _sync_skill_embeddings_to_db(runtime)

            # 缓存编译结果到文件，重启后自动恢复
            _save_runtime_cache(runtime)
            logger.info(
                f"[Compiler] 编译模式已激活: "
                f"{len(runtime.skills)} skills, {len(agents)} agents"
            )
            return runtime
        else:
            logger.warning("[Compiler] 编译无产出, 无可用 Agent（请配置业务域）")
    except Exception as e:
        logger.error(f"[Compiler] 编译失败: {e}")

    # 编译失败或无产出时，尝试从缓存恢复
    return _load_runtime_cache()


_RUNTIME_CACHE_FILE = os.path.join(os.path.dirname(__file__), "..", "..", "data", ".compiled_runtime.json")

def _save_runtime_cache(runtime):
    """保存编译结果到文件，供重启后恢复。"""
    try:
        import json as _json
        data = {
            "concept_count": runtime.concept_count,
            "skill_count": runtime.skill_count,
            "agent_count": runtime.agent_count,
            "chain_count": runtime.chain_count,
            "concept_map": runtime.concept_map,
            "agents": [{"name": a.name, "display_name": a.display_name, "icon": a.icon, "color": a.color,
                         "description": a.description, "project_description": a.project_description,
                         "skill_names": a.skill_names, "chain_names": a.chain_names}
                        for a in runtime.agents],
            "skills": [{"name": s.name, "display_name": s.display_name, "concept": s.concept,
                         "concept_label": s.concept_label, "data_source_type": s.data_source_type,
                         "triggers": s.triggers, "agent": s.agent,
                         "output_fields": s.output_fields, "skill_names": getattr(s, 'skill_names', []),
                         "action": getattr(s, 'action', ''), "action_label": getattr(s, 'action_label', '')}
                        for s in runtime.skills],
            "compiled_at": getattr(runtime, 'compiled_at', None),
        }
        os.makedirs(os.path.dirname(_RUNTIME_CACHE_FILE), exist_ok=True)
        # sanitize: 用 ensure_ascii=True 避免 surrogate 字符导致写入失败
        raw = _json.dumps(data, ensure_ascii=True, default=str)
        with open(_RUNTIME_CACHE_FILE, "w", encoding="ascii") as f:
            f.write(raw)
        logger.info(f"[Compiler] 缓存已保存: {_RUNTIME_CACHE_FILE}")
    except Exception as e:
        logger.warning(f"[Compiler] 缓存保存失败: {e}")
    except Exception as e:
        logger.warning(f"[Compiler] 缓存保存失败: {e}")


def _load_runtime_cache():
    """从文件恢复编译结果。"""
    try:
        if os.path.exists(_RUNTIME_CACHE_FILE):
            import json as _json
            with open(_RUNTIME_CACHE_FILE, "r", encoding="utf-8") as f:
                data = _json.load(f)
            # 构造一个简单的运行时对象供 compile_status 使用
            class CachedRuntime:
                pass
            rt = CachedRuntime()
            rt.concept_count = data.get("concept_count", 0)
            rt.skill_count = data.get("skill_count", 0)
            rt.agent_count = data.get("agent_count", 0)
            rt.chain_count = data.get("chain_count", 0)
            rt.concept_map = data.get("concept_map", {})
            rt.agents = data.get("agents", [])
            rt.skills = data.get("skills", [])
            rt.chains = []
            rt.compiled_at = data.get("compiled_at")
            global _compiled_runtime, _use_compiled
            _compiled_runtime = rt
            _use_compiled = True
            logger.info(f"[Compiler] 已从缓存恢复: {rt.concept_count}概念, {rt.skill_count}Skill")
            return rt
    except Exception as e:
        logger.warning(f"[Compiler] 缓存恢复失败: {e}")
    return None

    _use_compiled = False
    _loaded_agents.clear()
    _compiled_runtime = None
    return None


async def _sync_agents_to_db(runtime):
    """将编译器产出的 Agent 定义写入 agent.db。"""
    try:
        from app.db import get_db
        async for session in get_db():
            from app.repositories.agent_repository import AgentRepository
            repo = AgentRepository(session)
            for i, ad in enumerate(runtime.agents):
                try:
                    kwargs = dict(
                        display_name=ad.display_name, icon=ad.icon, color=ad.color,
                        description=ad.description, system_prompt=ad.system_prompt,
                        project_description=ad.project_description or "",
                        sort_order=len(runtime.agents) - i, enabled=True,
                    )
                    existing = await repo.get_by_name(ad.name)
                    if existing:
                        await repo.update(ad.name, **kwargs)
                    else:
                        await repo.create(name=ad.name, **kwargs)
                except Exception:
                    pass
            # 禁用在编译器产出中不存在的旧 Agent
            compiled_names = {ad.name for ad in runtime.agents}
            all_agents = await repo.get_all()
            for agent in all_agents:
                if agent.name not in compiled_names and agent.enabled:
                    await repo.update(agent.name, enabled=False)
                    logger.info(f"[Compiler] 禁用旧 Agent: {agent.name}")
            logger.info(f"[Compiler] {len(runtime.agents)} Agent 定义已同步到 agent.db")
    except Exception as e:
        logger.warning(f"[Compiler] Agent DB 同步失败: {e}")


async def _sync_chains_to_db(runtime):
    """将编译器产出的链定义写入 agent.db chains 表。
    标记 source='compiler'，仅覆盖/禁用编译器链，手动链不受影响。
    编译器 chain 为空时仍会禁用旧的编译器链。"""
    try:
        from app.db import get_db
        async for session in get_db():
            from app.repositories.chain_repo import ChainRepository
            repo = ChainRepository(session)
            active_ids = set()
            synced = 0
            for chain in (runtime.chains or [])[:20]:
                steps_list = []
                for i, step in enumerate(chain.steps):
                    steps_list.append(dict(
                        step_order=i,
                        step_id=step.get("step_id", f"step_{i}"),
                        description=step.get("description", ""),
                        agent_name="",
                        prompt_template=step.get("prompt_template", ""),
                        output_key=step.get("output_key", ""),
                        focus_concepts=step.get("focus_concepts", ""),
                    ))
                await repo.upsert(
                    chain_id=chain.name, name=chain.display_name,
                    description=chain.description,
                    triggers=list(chain.triggers),
                    final_prompt_template=chain.steps[-1].get("prompt_template", "") if chain.steps else "",
                    focus_concepts=",".join(chain.path),
                    enabled=True, source="compiler",
                    steps=steps_list,
                )
                active_ids.add(chain.name)
                synced += 1
            # 禁用 source='compiler' 但不在本次产出中的旧链
            all_chains = await repo.list_all()
            for chain in all_chains:
                if chain.source == "compiler" and chain.chain_id not in active_ids:
                    chain.enabled = False
            await session.commit()
            if synced > 0:
                logger.info(f"[Compiler] {synced} 编译器链已同步（手动链未受影响）")
            else:
                logger.info("[Compiler] 无编译器链产出，旧编译器链已禁用")
    except Exception as e:
        logger.warning(f"[Compiler] Chain DB 同步失败: {e}")


def get_compiled_runtime():
    """获取最近一次编译器产出。"""
    return _compiled_runtime


async def _sync_skill_triggers_to_db(runtime):
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
        from app.db import get_db
        async for session in get_db():
            from app.repositories.namespace_config_repo import NamespaceConfigRepository
            repo = NamespaceConfigRepository(session)
            existing = await repo.get(ns, "skill_overrides")
            # 合并：只补新 Skill，已有的保留用户自定义
            for s in runtime.skills:
                if s.name not in existing:
                    existing[s.name] = {"triggers": list(s.triggers)}
            await repo.save(ns, "skill_overrides", existing)
            if runtime.skills:
                logger.info(f"[Compiler] {len(runtime.skills)} Skill 触发词已同步到 DB")
    except Exception as e:
        logger.warning(f"[Compiler] Skill 触发词同步失败: {e}")


async def _sync_skill_embeddings_to_db(runtime):
    """编译后清除旧 embedding，首次 L2 分类时自动重建（使用和 candidates 同源的数据）。"""
    try:
        from app.db import get_db
        async for session in get_db():
            await session.execute("DELETE FROM agent_skill_embeddings")
            await session.commit()
        logger.info("[Compiler] 已清除旧 Skill embeddings（下次L2分类时自动重建）")
    except Exception as e:
        logger.warning(f"[Compiler] Skill embedding 清理失败: {e}")


