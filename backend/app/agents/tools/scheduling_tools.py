"""排产工具 — 模拟数据 + MES CLI 接入"""
import os
from typing import Any, Dict, List, Optional

from app.agents.tools.mes_cli_runner import cli_or_mock
from app.core.logger import log

MES_API_ENABLED = os.getenv("MES_API_ENABLED", "false").lower() == "true"


# ─── 模拟数据 ───
MOCK_SCHEDULES = [
    {"line": "SMT-01", "product": "主板A", "plan_qty": 500, "actual_qty": 380, "start": "08:00", "end": "17:00", "status": "进行中", "progress": 76},
    {"line": "SMT-02", "product": "控制板B", "plan_qty": 300, "actual_qty": 300, "start": "08:00", "end": "16:30", "status": "已完成", "progress": 100},
    {"line": "DIP-01", "product": "电源模块C", "plan_qty": 200, "actual_qty": 85, "start": "09:00", "end": "18:00", "status": "进行中", "progress": 42},
    {"line": "组装-01", "product": "成品D", "plan_qty": 150, "actual_qty": 0, "start": "14:00", "end": "22:00", "status": "待开始", "progress": 0},
    {"line": "组装-02", "product": "成品E", "plan_qty": 200, "actual_qty": 0, "start": "明日 08:00", "end": "明日 17:00", "status": "已排期", "progress": 0},
]

MOCK_CAPACITY = {
    "total_lines": 5,
    "active_lines": 2,
    "planned_lines": 3,
    "overall_progress": 68,
    "today_target": 1350,
    "today_actual": 765,
}


async def query_schedule(line: Optional[str] = None, date: Optional[str] = None) -> List[Dict[str, Any]]:
    """查询排产计划"""
    log.info(f"[排产工具] 查询排产, 产线: {line}, 日期: {date}")
    cmd = ["schedule", "query"]
    if line:
        cmd.extend(["--line", line])
    if date:
        cmd.extend(["--date", date])
    result = cli_or_mock(cmd, MOCK_SCHEDULES, MES_API_ENABLED)
    if isinstance(result, list):
        if line:
            return [s for s in result if line.lower() in s.get("line", "").lower()]
        return result
    return MOCK_SCHEDULES


async def query_capacity() -> Dict[str, Any]:
    """查询产能概况"""
    log.info("[排产工具] 查询产能概况")
    return cli_or_mock(["schedule", "capacity"], MOCK_CAPACITY, MES_API_ENABLED)


async def suggest_schedule(product: str = "", urgency: str = "normal") -> str:
    """排产建议"""
    log.info(f"[排产工具] 排产建议, 产品: {product}, 紧急度: {urgency}")
    cmd = ["schedule", "suggest"]
    if product:
        cmd.extend(["--product", product])
    if urgency:
        cmd.extend(["--urgency", urgency])
    result = cli_or_mock(cmd, MOCK_SCHEDULES, MES_API_ENABLED)
    if isinstance(result, list):
        suggestions = []
        for s in result:
            if s.get("status") == "待开始":
                suggestions.append(f"产线 {s['line']} 可安排 {s['product']}，计划 {s['plan_qty']} 件，{s['start']} 开始")
        if not suggestions:
            suggestions.append("当前所有产线已有排期，建议查看空闲时段或协调换线")
        return "\n".join(suggestions)
    return str(result)


def format_schedule_optimization(result: Dict[str, Any]) -> str:
    """格式化排产优化结果"""
    eval_data = result["evaluation"]
    lines = ["## 排产优化评估与优化方案\n"]
    lines.append(f"**综合评分**: {eval_data['overall_score']}/{eval_data['max_score']}")
    lines.append("")

    lines.append("### 各维度评分")
    for dim, score in eval_data["scores"].items():
        lines.append(f"  {dim}: {'★' * score}{'☆' * (5 - score)}")
    lines.append("")

    if eval_data["suggestions"]:
        lines.append("### 优化建议")
        for s in eval_data["suggestions"]:
            lines.append(f"  - {s}")
        lines.append("")

    if result.get("optimized_plan"):
        opt = result["optimized_plan"]
        lines.append("### 优化后方案")
        for k, v in opt.items():
            if k not in ("optimizations_applied",):
                initial = result["initial_plan"].get(k, "N/A")
                lines.append(f"  {k}: {initial} → {v}")
        if opt.get("optimizations_applied"):
            lines.append("")
            lines.append("### 已应用的优化")
            for o in opt["optimizations_applied"]:
                lines.append(f"  ✓ {o}")

    return "\n".join(lines)


def format_schedule(schedules: List[Dict[str, Any]]) -> str:
    """格式化排产数据为文本"""
    if not schedules:
        return "当前无排产计划。"
    lines = ["## 今日排产计划\n"]
    lines.append("| 产线 | 产品 | 计划 | 实际 | 进度 | 状态 |")
    lines.append("|------|------|------|------|------|------|")
    for s in schedules:
        lines.append(f"| {s['line']} | {s['product']} | {s['plan_qty']} | {s['actual_qty']} | {s['progress']}% | {s['status']} |")
    cap = MOCK_CAPACITY
    lines.append(f"\n**产能概况**: 总产线 {cap['total_lines']} 条，运行中 {cap['active_lines']} 条，今日目标 {cap['today_target']} 件，已完成 {cap['today_actual']} 件 ({cap['overall_progress']}%)")
    return "\n".join(lines)


async def optimize_schedule(plan: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """排产优化 — Evaluator-Optimizer 模式，真实数据输入 + Python 推荐算法"""
    from app.agents.evaluator import evaluate_scheduling_plan, optimize_scheduling_plan

    # MES_API_ENABLED 时从 CLI 拉真实 MO 列表 + 产线计划数据
    if MES_API_ENABLED:
        cli_data = cli_or_mock(["schedule", "optimize"], None, True)
        if isinstance(cli_data, dict) and cli_data:
            # 用真实数据构建初始方案供评估器分析
            plan = {**plan, "mes_data": cli_data} if plan else {"mes_data": cli_data}

    # 生成初始方案（使用当前排产数据作为基准）
    if plan:
        initial_plan = plan
    else:
        initial_plan = {
            "balance_rate": 72,
            "equipment_utilization": 65,
            "delivery_rate": 88,
            "changeovers": 5,
            "wip_count": 120,
        }

    # 评估
    evaluation = evaluate_scheduling_plan(initial_plan)

    # 优化
    if evaluation["needs_optimization"]:
        optimized_plan = await optimize_scheduling_plan(initial_plan, evaluation)
        return {
            "initial_plan": initial_plan,
            "evaluation": evaluation,
            "optimized_plan": optimized_plan,
            "mode": "evaluator_optimizer",
        }
    else:
        return {
            "initial_plan": initial_plan,
            "evaluation": evaluation,
            "mode": "no_optimization_needed",
        }
