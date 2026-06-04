"""任务优先级评估器 — 基于关键词+上下文判断协作任务优先级

配置从 config/collaboration.yaml 加载。
"""
from typing import List, Tuple

from app.core.config_loader import load_yaml

_cfg = load_yaml("collaboration")

URGENCY_KEYWORDS = _cfg.get("urgency_keywords", {"high": [], "medium": []})
AGENT_PRIORITY_WEIGHT = _cfg.get("agent_priority_weight", {})


def evaluate_priority(message: str, agent_name: str) -> Tuple[str, int, str]:
    """评估单个 Agent 任务的优先级

    Returns:
        (priority, score, reason) — priority: high/medium/low, score: 0-100
    """
    score = AGENT_PRIORITY_WEIGHT.get(agent_name, 30)
    matched_reasons = []

    for kw in URGENCY_KEYWORDS.get("high", []):
        if kw in message:
            score += 30
            matched_reasons.append(kw)
            break

    for kw in URGENCY_KEYWORDS.get("medium", []):
        if kw in message:
            score += 15
            matched_reasons.append(kw)
            break

    score = min(score, 100)

    if score >= 80:
        priority = "high"
    elif score >= 55:
        priority = "medium"
    else:
        priority = "low"

    reason = f"{agent_name} 权重={AGENT_PRIORITY_WEIGHT.get(agent_name, 30)}" + (
        f", 匹配关键词: {','.join(matched_reasons)}" if matched_reasons else ""
    )
    return priority, score, reason


def prioritize_agents(message: str, agent_names: List[str]) -> List[Tuple[str, str, int, str]]:
    """对协作 Agent 列表按优先级排序

    Returns:
        [(agent_name, priority, score, reason), ...] — 按 score 降序排列
    """
    evaluated = [
        (name, *evaluate_priority(message, name))
        for name in agent_names
    ]
    evaluated.sort(key=lambda x: x[2], reverse=True)
    return evaluated
