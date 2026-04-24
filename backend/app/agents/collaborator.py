"""Agent 协作引擎 — 跨 Agent 工具调用与结果聚合"""
import asyncio
from typing import Dict, Any, Optional, List, Tuple

# 显式触发协作的关键词
COLLABORATION_KEYWORDS = [
    "整体情况", "综合分析", "全面", "协作", "全部查一下", "汇总",
    "总体", "全局", "综合一下", "所有", "全部", "汇总一下",
]

# 隐式多领域意图关键词 — 用户未指定领域但问题天然涉及多个 Agent
IMPLICIT_COLLAB_KEYWORDS = [
    "生产线", "产线", "车间", "工厂",
    "今天", "今日", "目前", "现在", "当前状况", "当前情况",
    "怎么样", "什么状况", "情况如何", "生产情况", "运行状况",
    "运营", "概览", "看板",
]

# 参与协作的 Agent 列表及其查询消息模板
COLLAB_AGENTS = [
    ("scheduling", "查询当前排产计划和产能情况"),
    ("equipment", "查询设备运行状态和故障信息"),
    ("quality", "质量概况和合格率"),
    ("inventory", "查询物料库存和齐套情况"),
]


def should_collaborate(message: str, use_agent: bool) -> bool:
    """判断是否触发多 Agent 协作"""
    if use_agent:
        return True
    # 即使 use_agent 为 false，检测到显式协作关键词也自动触发
    return any(kw in message for kw in COLLABORATION_KEYWORDS)


def detect_collab_intent(message: str) -> bool:
    """
    二层路由：检测隐式协作意图
    当关键词路由未匹配时，检查消息是否包含多领域自然表达
    """
    has_domain = any(kw in message for kw in IMPLICIT_COLLAB_KEYWORDS)
    if not has_domain:
        return False
    # 有领域词但无明确 Agent 关键词 → 视为协作意图
    from app.agents.keywords import INTENT_KEYWORDS
    return not any(kw in message for kws in INTENT_KEYWORDS.values() for kw in kws)


async def invoke_agent_tool(agent_name: str, message: str) -> Tuple[str, Optional[str]]:
    """
    调用指定 Agent 的 call_tools，返回 (agent_name, tool_result)

    Args:
        agent_name: Agent 名称
        message: 用户消息（用于关键词匹配）

    Returns:
        (agent_name, 工具返回的格式化文本 或 None)
    """
    try:
        from app.agents import get_agent
        agent = get_agent(agent_name)
        result = await agent.call_tools(message)
        return agent_name, result
    except Exception as e:
        from app.core.logger import log
        log.warning(f"调用 Agent {agent_name} 工具失败: {e}")
        return agent_name, None


async def parallel_invoke(message: str, agent_configs: Optional[List[Tuple[str, str]]] = None) -> Dict[str, Any]:
    """
    并发调用多个 Agent 的工具，聚合结果

    Args:
        message: 用户消息
        agent_configs: [(agent_name, domain_query), ...]，默认使用 COLLAB_AGENTS

    Returns:
        {agent_name: tool_result_or_None, ...} 加 overall_status 和 summary
    """
    configs = agent_configs or COLLAB_AGENTS
    # 使用每个 Agent 的领域查询消息（而非用户原始消息），确保关键词匹配
    tasks = [invoke_agent_tool(name, domain_query) for name, domain_query in configs]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    aggregated = {}
    success_count = 0
    for result in results:
        if isinstance(result, Exception):
            from app.core.logger import log
            log.warning(f"Agent 调用异常: {result}")
            continue
        agent_name, tool_result = result
        aggregated[agent_name] = tool_result
        if tool_result:
            success_count += 1

    # 判断整体状态
    if success_count == len(configs):
        overall_status = "complete"
    elif success_count > 0:
        overall_status = "partial"
    else:
        overall_status = "failed"

    aggregated["overall_status"] = overall_status
    aggregated["success_count"] = success_count
    aggregated["total_count"] = len(configs)
    return aggregated


def format_collab_report(aggregated: Dict[str, Any]) -> str:
    """将协作结果格式化为 markdown 报告"""
    status = aggregated.get("overall_status", "unknown")
    success = aggregated.get("success_count", 0)
    total = aggregated.get("total_count", 0)

    lines = [f"## 综合查询报告\n"]
    lines.append(f"**查询状态**: {success}/{total} 个 Agent 返回数据\n")

    for agent_name, result in aggregated.items():
        if agent_name in ("overall_status", "success_count", "total_count"):
            continue
        if result:
            lines.append(f"### {agent_name}")
            lines.append(result)
            lines.append("")
        else:
            lines.append(f"### {agent_name}: 无匹配数据")
            lines.append("")

    return "\n".join(lines)


def get_collaboration_context() -> str:
    """返回协作能力描述（用于构建 system prompt）"""
    lines = ["你可以通过调用专业 Agent 的工具获取更全面的信息。"]
    lines.append("可用专业 Agent：")
    for name, desc in COLLAB_AGENTS:
        from app.agents import get_agent
        try:
            agent = get_agent(name)
            lines.append(f"- {agent.display_name}({name}): {desc}")
        except Exception:
            lines.append(f"- {name}: {desc}")
    return "\n".join(lines)
