"""Agent 意图路由器 — 三层：多域检测 → 关键词 → 隐式协作检测"""
from typing import Optional, Dict, Any, List
from app.core.logger import log
from app.agents.keywords import INTENT_KEYWORDS
from app.agents.settings import (
    COMPLEXITY_KEYWORDS,
    COMPLEXITY_LENGTH_THRESHOLDS,
    COMPLEXITY_MULTI_DOMAIN_BONUS,
    COMPLEXITY_RANGE,
    MODEL_SELECTION_THRESHOLDS,
    MODEL_SELECTION_MAP,
    COLLABORATION_KEYWORDS,
    IMPLICIT_COLLAB_KEYWORDS,
)
from app.core.resource_monitor import resource_monitor, ResourceTier


async def route_intent(message: str, agent_name: Optional[str] = None) -> Dict[str, Any]:
    """
    三层路由判断用户消息应路由到哪个 Agent

    Returns:
        {agent_name, confidence, method, use_agent, matched_agents}
    """
    if agent_name and agent_name != "auto":
        return {
            "agent_name": agent_name,
            "confidence": 1.0,
            "method": "manual",
            "use_agent": False,
            "matched_agents": [],
        }

    # 第零层：显式协作关键词检测（"协作"、"综合分析"等）
    if any(kw in message for kw in COLLABORATION_KEYWORDS):
        log.info(f"显式协作关键词命中 (消息: {message[:30]})")
        return {
            "agent_name": "general",
            "confidence": 0.8,
            "method": "explicit_collab",
            "use_agent": True,
            "matched_agents": [],
        }

    # 第一层：多领域关键词检测
    matched_agents: List[str] = []
    for agent_key, keywords in INTENT_KEYWORDS.items():
        for kw in keywords:
            if kw in message:
                matched_agents.append(agent_key)
                break

    if len(matched_agents) >= 2:
        log.info(f"多领域关键词检测，触发协作 (匹配: {matched_agents})")
        return {
            "agent_name": "general",
            "confidence": 0.7,
            "method": "multi_domain",
            "use_agent": True,
            "matched_agents": matched_agents,
        }

    if len(matched_agents) == 1:
        agent_key = matched_agents[0]
        log.info(f"关键词匹配路由到 {agent_key}")
        return {
            "agent_name": agent_key,
            "confidence": 0.85,
            "method": "keyword",
            "use_agent": False,
            "matched_agents": matched_agents,
        }

    # 第二层：隐式协作意图检测
    has_domain = any(kw in message for kw in IMPLICIT_COLLAB_KEYWORDS)
    if has_domain and not any(kw in message for kws in INTENT_KEYWORDS.values() for kw in kws):
        log.info(f"隐式协作意图检测命中 (消息: {message[:30]})")
        return {
            "agent_name": "general",
            "confidence": 0.6,
            "method": "collab_intent",
            "use_agent": True,
            "matched_agents": [],
        }

    # 默认通用助手
    log.info(f"未匹配到明确意图，默认路由到 general (消息: {message[:30]})")
    return {
        "agent_name": "general",
        "confidence": 0.3,
        "method": "default",
        "use_agent": False,
        "matched_agents": [],
    }


def assess_query_complexity(message: str) -> int:
    """评估查询复杂度（1-10 分）"""
    score = 0

    for kw, weight in COMPLEXITY_KEYWORDS.items():
        if kw in message:
            score += weight

    if len(message) > COMPLEXITY_LENGTH_THRESHOLDS["long"]:
        score += 2
    elif len(message) > COMPLEXITY_LENGTH_THRESHOLDS["short"]:
        score += 1

    matched = sum(1 for kws in INTENT_KEYWORDS.values() if any(k in message for k in kws))
    if matched >= 2:
        score += COMPLEXITY_MULTI_DOMAIN_BONUS

    return min(max(score, COMPLEXITY_RANGE[0]), COMPLEXITY_RANGE[1])


def select_model_for_complexity(message: str, user_model: Optional[str] = None) -> Optional[str]:
    """根据查询复杂度自动选择模型"""
    if user_model:
        return user_model

    complexity = assess_query_complexity(message)
    thresholds = MODEL_SELECTION_THRESHOLDS

    if complexity <= thresholds["simple_max"]:
        model = MODEL_SELECTION_MAP["simple"]
    elif complexity <= thresholds["medium_max"]:
        model = None
    else:
        model = MODEL_SELECTION_MAP["complex"]

    if resource_monitor.enabled and resource_monitor.current_tier in (ResourceTier.CONSTRAINED, ResourceTier.CRITICAL):
        return resource_monitor.get_recommended_model(model)
    return model
