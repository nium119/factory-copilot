"""Agent 意图路由器 — 关键词优先，禁用 LLM 回退以避免延迟"""
from typing import Optional, Dict, Any
from app.core.logger import log
from app.agents import AUTO_ROUTE_CONFIDENCE_THRESHOLD, get_intent_keywords


async def route_intent(message: str, agent_name: Optional[str] = None) -> Dict[str, Any]:
    """
    判断用户消息应路由到哪个 Agent

    Args:
        message: 用户消息内容
        agent_name: 若前端已指定则直接返回

    Returns:
        {agent_name, confidence, method}
    """
    if agent_name and agent_name != "auto":
        return {"agent_name": agent_name, "confidence": 1.0, "method": "manual"}

    # 1. 关键词匹配（从数据库动态加载）
    keywords_map = get_intent_keywords()
    for agent_key, keywords in keywords_map.items():
        for kw in keywords:
            if kw in message:
                log.info(f"关键词匹配路由到 {agent_key} (关键词: {kw})")
                return {
                    "agent_name": agent_key,
                    "confidence": 0.85,
                    "method": "keyword",
                }

    # 2. 默认通用助手（禁用 LLM 意图分类以避免 10+s 延迟）
    log.info(f"未匹配到明确意图，默认路由到 general (消息: {message[:30]})")
    return {"agent_name": "general", "confidence": 0.3, "method": "default"}
