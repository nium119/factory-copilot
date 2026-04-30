"""Agent 意图路由器 — 支持关键词路由和 LLM 路由两种策略"""
import json
from typing import Any, Dict, List, Optional

from app.agents.agent_config import AGENT_DEFINITIONS
from app.agents.settings import (
    COLLABORATION_KEYWORDS,
    COMPLEXITY_KEYWORDS,
    COMPLEXITY_LENGTH_THRESHOLDS,
    COMPLEXITY_MULTI_DOMAIN_BONUS,
    COMPLEXITY_RANGE,
    IMPLICIT_COLLAB_KEYWORDS,
    MODEL_SELECTION_MAP,
    MODEL_SELECTION_THRESHOLDS,
)
from app.core.logger import log
from app.core.resource_monitor import ResourceTier, resource_monitor

# 关键词路由表：从 agent_config 单一数据源提取
INTENT_KEYWORDS = {name: cfg["keywords"] for name, cfg in AGENT_DEFINITIONS.items() if cfg.get("keywords")}


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


# ── LLM 路由 ──────────────────────────────────────────────

def _build_routing_prompt(message: str) -> str:
    """构建 LLM 路由提示词，包含所有 Agent 的元数据和示例"""
    agent_list = []
    for name, cfg in AGENT_DEFINITIONS.items():
        if not cfg.get("enabled", True):
            continue
        keywords = cfg.get("keywords", [])
        kw_str = "、".join(keywords) if keywords else "（无关键词，兜底 Agent）"
        agent_list.append(
            f"- **{name}**（{cfg['display_name']}）：{cfg['description']}\n"
            f"  关键词：{kw_str}"
        )

    agents_text = "\n".join(agent_list)

    return f"""你是一个制造业 MES 系统的智能路由助手。根据用户消息，判断应该由哪个 Agent 处理。

## 可用 Agent 列表

{agents_text}

## 路由规则

1. 如果用户消息明确包含某个 Agent 的关键词或领域术语，选择对应 Agent
2. 如果消息涉及多个领域，判断最主要的需求，选择最匹配的单个 Agent
3. 如果消息包含"整体情况"、"综合分析"、"全面"、"协作"等词，设置 use_agent=true，agent_name 填 "general"
4. 如果无法判断，选择 "general"
5. confidence 取值：0.9-1.0=高度确定，0.7-0.89=比较确定，0.5-0.69=不确定，0.3-0.49=猜测

## 用户消息

{message}

## 输出格式

严格输出 JSON，不要包含其他文字：
{{"agent_name": "<agent key>", "confidence": <0.0-1.0>, "use_agent": <true|false>, "matched_agents": []}}"""


async def route_intent_llm(message: str, agent_name: Optional[str] = None) -> Dict[str, Any]:
    """
    LLM 路由：使用大模型判断用户消息应路由到哪个 Agent

    相比关键词路由的优势：
    - 理解语义和上下文，不依赖精确关键词命中
    - 能处理同义词、口语化表达、隐含意图
    - 对模糊查询有更好的鲁棒性

    代价：一次 LLM 调用延迟（通常 0.5-3s）
    """
    if agent_name and agent_name != "auto":
        return {
            "agent_name": agent_name,
            "confidence": 1.0,
            "method": "manual",
            "use_agent": False,
            "matched_agents": [],
        }

    try:
        from app.services.llm_service import llm_service

        prompt = _build_routing_prompt(message)

        # 使用同步调用获取路由结果，3s 超时保护
        import asyncio
        raw = await asyncio.wait_for(
            llm_service.chat_sync(
                message=prompt,
                system_prompt="你是一个精确的 JSON 路由决策器。只输出 JSON，不要包含其他内容。",
            ),
            timeout=5.0,
        )

        # 提取 JSON（处理可能的 markdown 代码块包裹）
        raw = raw.strip()
        if raw.startswith("```"):
            lines = raw.split("\n")
            raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
            raw = raw.strip()

        result = json.loads(raw)
        result.setdefault("method", "llm")
        result.setdefault("matched_agents", [])
        result.setdefault("use_agent", False)
        result.setdefault("confidence", 0.5)

        log.info(
            f"LLM 路由 → {result['agent_name']} "
            f"(confidence={result['confidence']}, use_agent={result['use_agent']})"
        )
        return result

    except asyncio.TimeoutError:
        log.warning("LLM 路由超时，回退到关键词路由")
    except json.JSONDecodeError as e:
        log.warning(f"LLM 路由 JSON 解析失败: {e}，原始输出: {raw[:200]}")
    except Exception as e:
        log.error(f"LLM 路由失败: {e}")

    # 回退到关键词路由
    return await route_intent(message, agent_name)


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
