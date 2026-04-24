"""排产工具 — 模拟数据 + 预留 MES API 接入"""
from typing import Dict, Any, Optional, List
from app.core.logger import log

# TODO: 接入真实 MES API 时替换此配置
MES_API_BASE = "http://localhost:9090"  # 预留 MES API 地址
MES_API_ENABLED = False


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


async def query_schedule(line: Optional[str] = None) -> List[Dict[str, Any]]:
    """查询排产计划"""
    log.info(f"[排产工具] 查询排产, 产线: {line}")
    if MES_API_ENABLED:
        # TODO: 调用 MES API
        pass
    if line:
        return [s for s in MOCK_SCHEDULES if line.lower() in s["line"].lower()]
    return MOCK_SCHEDULES


async def query_capacity() -> Dict[str, Any]:
    """查询产能概况"""
    log.info("[排产工具] 查询产能概况")
    return MOCK_CAPACITY


async def suggest_schedule(product: str = "", urgency: str = "normal") -> str:
    """排产建议"""
    log.info(f"[排产工具] 排产建议, 产品: {product}, 紧急度: {urgency}")
    suggestions = []
    for s in MOCK_SCHEDULES:
        if s["status"] == "待开始":
            suggestions.append(f"产线 {s['line']} 可安排 {s['product']}，计划 {s['plan_qty']} 件，{s['start']} 开始")
    if not suggestions:
        suggestions.append("当前所有产线已有排期，建议查看空闲时段或协调换线")
    return "\n".join(suggestions)


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
