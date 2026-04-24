"""Agent 意图路由器 — 三层：多域检测 → 关键词 → 隐式协作检测"""
from typing import Optional, Dict, Any
from app.core.logger import log
from app.agents.keywords import INTENT_KEYWORDS
from app.agents import collaborator


async def route_intent(message: str, agent_name: Optional[str] = None) -> Dict[str, Any]:
    """
    三层路由判断用户消息应路由到哪个 Agent

    第一层：多领域关键词检测（涉及多个 Agent → 协作模式）
    第二层：单关键词匹配（精确路由）
    第三层：隐式协作意图检测（自然语言的多领域表达）

    Returns:
        {agent_name, confidence, method, use_agent}
        use_agent: 是否自动触发协作模式
    """
    if agent_name and agent_name != "auto":
        return {"agent_name": agent_name, "confidence": 1.0, "method": "manual", "use_agent": False}

    # 第一层：多领域关键词检测
    matched_agents = []
    for agent_key, keywords in INTENT_KEYWORDS.items():
        for kw in keywords:
            if kw in message:
                matched_agents.append((agent_key, kw))
                break

    if len(matched_agents) >= 2:
        agent_names = [a[0] for a in matched_agents]
        log.info(f"多领域关键词检测，触发协作 (匹配: {agent_names})")
        return {
            "agent_name": "general",
            "confidence": 0.7,
            "method": "multi_domain",
            "use_agent": True,
        }

    if len(matched_agents) == 1:
        agent_key, kw = matched_agents[0]
        log.info(f"关键词匹配路由到 {agent_key} (关键词: {kw})")
        return {
            "agent_name": agent_key,
            "confidence": 0.85,
            "method": "keyword",
            "use_agent": False,
        }

    # 第二层：隐式协作意图检测
    if collaborator.detect_collab_intent(message):
        log.info(f"隐式协作意图检测命中 (消息: {message[:30]})")
        return {
            "agent_name": "general",
            "confidence": 0.6,
            "method": "collab_intent",
            "use_agent": True,
        }

    # 默认通用助手
    log.info(f"未匹配到明确意图，默认路由到 general (消息: {message[:30]})")
    return {"agent_name": "general", "confidence": 0.3, "method": "default", "use_agent": False}
