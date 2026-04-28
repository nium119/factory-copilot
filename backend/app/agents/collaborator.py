"""Agent 协作引擎 — 跨 Agent 工具调用与结果聚合"""
import asyncio
from typing import Dict, Any, Optional, List, Tuple

from app.agents.settings import (
    COLLABORATION_KEYWORDS,
    IMPLICIT_COLLAB_KEYWORDS,
    COLLAB_DOMAIN_QUERIES,
    RETRY_CONFIG,
)


def should_collaborate(message: str, use_agent: bool) -> bool:
    """判断是否触发多 Agent 协作"""
    if use_agent:
        return True
    return any(kw in message for kw in COLLABORATION_KEYWORDS)


def detect_collab_intent(message: str) -> bool:
    """
    二层路由：检测隐式协作意图
    当关键词路由未匹配时，检查消息是否包含多领域自然表达
    """
    has_domain = any(kw in message for kw in IMPLICIT_COLLAB_KEYWORDS)
    if not has_domain:
        return False
    from app.agents.keywords import INTENT_KEYWORDS
    return not any(kw in message for kws in INTENT_KEYWORDS.values() for kw in kws)


async def invoke_agent_tool(agent_name: str, message: str) -> Tuple[str, Optional[str]]:
    """
    调用指定 Agent 的 call_tools，带重试和降级

    Returns:
        (agent_name, 工具返回的格式化文本 或 None)
    """
    from app.core.logger import log
    last_error = None
    max_retries = RETRY_CONFIG["max_retries"]

    for attempt in range(max_retries + 1):
        try:
            from app.agents import get_agent
            agent = get_agent(agent_name)
            result = await agent.call_tools(message)
            if result:
                return agent_name, result
            if attempt < max_retries:
                log.warning(f"Agent {agent_name} 返回空结果，重试 {attempt + 1}/{max_retries}")
                await asyncio.sleep(RETRY_CONFIG["empty_result_delay"])
                continue
            return agent_name, None
        except Exception as e:
            last_error = e
            if attempt < max_retries:
                log.warning(f"Agent {agent_name} 调用失败 (尝试 {attempt + 1}/{max_retries}): {e}")
                await asyncio.sleep(RETRY_CONFIG["exception_delay"])
            else:
                log.warning(f"调用 Agent {agent_name} 工具失败，已达最大重试: {e}")

    return agent_name, f"[{agent_name} 调用失败: {last_error}]"


def get_collab_agents() -> List[Tuple[str, str]]:
    """从注册表动态发现协作 Agent —— 仅包含 COLLAB_DOMAIN_QUERIES 中明确配置的领域（默认协作时排除安灯、工位等操作专用 Agent）"""
    from app.agents import _AGENT_REGISTRY
    core_agents = set(COLLAB_DOMAIN_QUERIES.keys())
    return [
        (name, COLLAB_DOMAIN_QUERIES[name])
        for name in _AGENT_REGISTRY
        if name in core_agents
    ]


async def parallel_invoke(message: str, agent_configs: Optional[List[Tuple[str, str]]] = None) -> Dict[str, Any]:
    """
    并发调用多个 Agent 的工具，聚合结果
    """
    configs = agent_configs or get_collab_agents()
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
    from app.agents.agent_config import get_agent_metadata
    lines = ["你可以通过调用专业 Agent 的工具获取更全面的信息。"]
    lines.append("可用专业 Agent：")
    for name, desc in get_collab_agents():
        info = get_agent_metadata(name)
        lines.append(f"- {info['display_name']}({name}): {desc}")
    return "\n".join(lines)
