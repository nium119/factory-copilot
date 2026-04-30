"""Evaluator-Optimizer 模式 — 生成 → 评估 → 优化循环"""
from typing import Any, Dict

from app.agents.settings import (
    EVAL_OPTIMIZATION_THRESHOLD,
    EVAL_SCORE_THRESHOLDS,
    EVALUATION_CRITERIA,
)
from app.core.logger import log


def evaluate_scheduling_plan(plan: Dict[str, Any]) -> Dict[str, Any]:
    """评估排产方案质量"""
    log.info("[Evaluator] 评估排产方案")
    scores = {}
    suggestions = []
    criteria = EVALUATION_CRITERIA["scheduling"]
    thresholds = EVAL_SCORE_THRESHOLDS

    # 产线平衡率
    balance_rate = plan.get("balance_rate", 70)
    t = thresholds["balance_rate"]
    if balance_rate >= t["excellent"]:
        scores["产线平衡率"] = 5
    elif balance_rate >= t["good"]:
        scores["产线平衡率"] = 3
        suggestions.append("建议优化工序分配，减少瓶颈工序等待时间")
    else:
        scores["产线平衡率"] = 2
        suggestions.append("产线平衡率偏低，建议重新分配工序负荷")

    # 设备利用率
    utilization = plan.get("equipment_utilization", 60)
    t = thresholds["equipment_utilization"]
    if utilization >= t["excellent"]:
        scores["设备利用率"] = 5
    elif utilization >= t["good"]:
        scores["设备利用率"] = 3
        suggestions.append("设备利用率有提升空间，可考虑合并相邻工单")
    else:
        scores["设备利用率"] = 2
        suggestions.append("设备利用率偏低，建议合并工单或调整排班")

    # 交期达成率
    delivery_rate = plan.get("delivery_rate", 90)
    t = thresholds["delivery_rate"]
    if delivery_rate >= t["excellent"]:
        scores["交期达成率"] = 5
    elif delivery_rate >= t["good"]:
        scores["交期达成率"] = 3
        suggestions.append("部分工单可能延期，建议增加产能或调整优先级")
    else:
        scores["交期达成率"] = 2
        suggestions.append("交期达成率不达标，需紧急调整排产计划")

    # 换线次数
    changeovers = plan.get("changeovers", 5)
    t = thresholds["changeovers"]
    if changeovers <= t["excellent"]:
        scores["换线次数"] = 5
    elif changeovers <= t["good"]:
        scores["换线次数"] = 3
        suggestions.append("换线次数偏多，建议按产品族分组排产")
    else:
        scores["换线次数"] = 2
        suggestions.append("换线频繁，建议优化产品分组")

    # 在制品数量
    wip = plan.get("wip_count", 100)
    t = thresholds["wip_count"]
    if wip <= t["excellent"]:
        scores["在制品数量"] = 5
    elif wip <= t["good"]:
        scores["在制品数量"] = 3
        suggestions.append("在制品数量适中，可进一步减少")
    else:
        scores["在制品数量"] = 2
        suggestions.append("在制品积压，建议加快流转速度")

    # 加权综合得分
    weights = [c["weight"] for c in criteria]
    values = list(scores.values())
    overall = sum(s * w for s, w in zip(values, weights))

    return {
        "scores": scores,
        "overall_score": round(overall, 2),
        "max_score": 5.0,
        "suggestions": suggestions,
        "needs_optimization": overall < EVAL_OPTIMIZATION_THRESHOLD,
    }


async def optimize_scheduling_plan(original_plan: Dict[str, Any], evaluation: Dict[str, Any]) -> Dict[str, Any]:
    """基于评估结果优化排产方案"""
    log.info(f"[Optimizer] 优化排产方案 (评估得分: {evaluation['overall_score']})")

    optimized = dict(original_plan)
    optimizations = []

    improvements = {
        "产线平衡率": ("balance_rate", 10, 95),
        "设备利用率": ("equipment_utilization", 8, 92),
        "交期达成率": ("delivery_rate", 5, 99),
        "换线次数": ("changeovers", -2, 1),
        "在制品数量": ("wip_count", -20, 20),
    }

    messages = {
        "产线平衡率": "重新分配工序负荷，平衡各工位工作时间",
        "设备利用率": "合并相邻工单，减少设备空闲时间",
        "交期达成率": "调整优先级，紧急工单优先排产",
        "换线次数": "按产品族分组，减少换线次数",
        "在制品数量": "加快物料流转，减少在制品积压",
    }

    for dim, (key, delta, limit) in improvements.items():
        if evaluation["scores"].get(dim, 5) < 4:
            current = original_plan.get(key, 70)
            if delta > 0:
                optimized[key] = min(current + delta, limit)
            else:
                optimized[key] = max(current + delta, limit)
            optimizations.append(messages[dim])

    optimized["optimizations_applied"] = optimizations
    return optimized
