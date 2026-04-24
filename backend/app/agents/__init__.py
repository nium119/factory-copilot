"""Agent 注册表与意图路由配置"""
import json
import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "agent.db")

# Agent 关键词映射（用于正则意图匹配）
INTENT_KEYWORDS = {
    "scheduling": ["排产", "排期", "计划", "调度", "排班", "产线安排", "生产计划", "工单排程", "产能"],
    "quality": ["质检", "质量", "不合格", "次品", "良率", "检测", "抽检", "返工", "报废", "不良", "合格率", "SPC"],
    "equipment": ["设备", "故障", "维修", "保养", "停机", "OEE", "开机率", "设备状态", "点检", "巡检", "备件"],
    "inventory": ["库存", "物料", "仓库", "缺料", "盘点", "出入库", "备料", "发料", "领料", "物料状态"],
    "process": ["工艺", "流程", "SOP", "工序", "参数", "工艺路线", "BOM", "工艺卡", "操作规范", "工艺优化"],
    "production_prep": ["生产准备", "物料齐套", "设备确认", "模具准备", "质检标准", "工单准备", "投产准备", "齐套检查", "工序准备"],
    "andon": ["安灯", "异常", "停线", "报警", "呼叫", "问题上报", "应急响应", "故障报警", "产线异常", "andon"],
    "workstation": ["工位", "终端", "报工", "开工", "完工", "SOP", "首件确认", "自检", "签到", "点检", "异常上报", "领料", "产量上报"],
}

# Agent 自动路由置信度阈值
AUTO_ROUTE_CONFIDENCE_THRESHOLD = 0.7

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
    """将数据库配置应用到 Agent 实例"""
    if config:
        if config.get("display_name"):
            agent.display_name = config["display_name"]
        if config.get("icon"):
            agent.icon = config["icon"]
        if config.get("color"):
            agent.color = config["color"]
        if config.get("description"):
            agent.description = config["description"]
        if config.get("system_prompt"):
            agent.system_prompt = config["system_prompt"]
        if config.get("keywords"):
            try:
                kw = config["keywords"]
                agent.keywords = json.loads(kw) if isinstance(kw, str) else kw
            except (json.JSONDecodeError, TypeError):
                pass
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


def _safe_json(value, default=None):
    """安全解析 JSON"""
    if value is None:
        return default or []
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default or []


def get_agents_from_db():
    """从数据库获取 Agent 信息列表（给 API 用）"""
    configs = _load_all_agent_configs()
    result = []
    for cfg in configs:
        if cfg["name"] in _AGENT_REGISTRY:
            agent = get_agent(cfg["name"])
            info = agent.get_info()
            info["enabled"] = cfg.get("enabled", True)
            info["roles"] = _safe_json(cfg.get("roles"))
            result.append(info)
        else:
            result.append({
                "name": cfg["name"],
                "display_name": cfg["display_name"],
                "icon": cfg["icon"],
                "color": cfg["color"],
                "description": cfg["description"],
                "enabled": cfg.get("enabled", True),
                "roles": _safe_json(cfg.get("roles")),
            })
    return result


def get_intent_keywords():
    """从数据库加载意图关键词（优先数据库，fallback 到代码）"""
    configs = _load_all_agent_configs()
    keywords = {}
    for cfg in configs:
        if cfg.get("keywords"):
            kw_list = _safe_json(cfg["keywords"])
            if kw_list:
                keywords[cfg["name"]] = kw_list
    # 如果数据库有关键词就返回，否则使用硬编码 fallback
    return keywords if keywords else INTENT_KEYWORDS
