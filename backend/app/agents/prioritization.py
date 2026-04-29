"""任务优先级评估器 — 基于关键词+上下文判断协作任务优先级"""
from typing import List, Tuple

# 紧急关键词 → 对应 Agent 及优先级提升
URGENCY_KEYWORDS = {
    "high": [
        "紧急", "故障", "停线", "异常", "报警", "告警", "事故",
        "宕机", "死机", "崩溃", "损坏", "危险", "立即", "马上",
        "立刻", "赶紧", "严重", "重大", "紧急停机", "急停",
    ],
    "medium": [
        "缺料", "延期", "延迟", "预警", "偏差", "不合格",
        "质量问题", "等待", "排队", "卡住", "积压",
    ],
}

# Agent 固有优先级权重（用于同等级细排序）
AGENT_PRIORITY_WEIGHT = {
    "andon": 90,          # 安灯 — 最高，涉及停线安全
    "equipment": 80,      # 设备 — 故障直接影响生产
    "quality": 75,        # 质检 — 质量问题
    "inventory": 60,      # 线边仓 — 缺料
    "production_prep": 55,# 生产准备
    "scheduling": 50,     # 排产
    "workstation": 45,    # 工位
    "process": 40,        # 工艺
    "monitor": 35,        # 监控
    "general": 30,        # 通用
}


def evaluate_priority(message: str, agent_name: str) -> Tuple[str, int, str]:
    """评估单个 Agent 任务的优先级

    Returns:
        (priority, score, reason) — priority: high/medium/low, score: 0-100
    """
    score = AGENT_PRIORITY_WEIGHT.get(agent_name, 30)
    matched_reasons = []

    for kw in URGENCY_KEYWORDS["high"]:
        if kw in message:
            score += 30
            matched_reasons.append(kw)
            break  # 只加一次 high 分

    for kw in URGENCY_KEYWORDS["medium"]:
        if kw in message:
            score += 15
            matched_reasons.append(kw)
            break  # 只加一次 medium 分

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
    # 按 score 降序
    evaluated.sort(key=lambda x: x[2], reverse=True)
    return evaluated
