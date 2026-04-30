"""KPI 目标监控工具 — 模拟数据 + MES CLI 接入"""
import os
from datetime import datetime
from typing import Any, Dict, Optional

from app.agents.settings import MANUFACTURING_KPIS, get_kpi_status
from app.agents.tools.mes_cli_runner import cli_or_mock
from app.core.logger import log

MES_API_ENABLED = os.getenv("MES_API_ENABLED", "false").lower() == "true"

MOCK_KPI_ACTUALS = {
    # 设备
    "oee": 78.5,
    "equipment_uptime": 93.2,
    "mtbf": 165.0,
    "mttr": 45.0,
    # 质量
    "yield_rate": 96.5,
    "defect_rate": 3.5,
    "cpk": 1.15,
    # 排产
    "delivery_rate": 88.0,
    "balance_rate": 72.0,
    "changeover_time": 42.0,
    # 库存
    "inventory_turnover": 9.5,
    "shortage_rate": 1.8,
    # 安灯
    "andon_response_time": 8.0,
    "andon_resolve_time": 55.0,
    # 生产
    "production_output": 880.0,
}

MOCK_KPI_TREND = {
    "oee": [80.2, 79.8, 79.1, 78.9, 78.5, 78.3, 78.5],
    "yield_rate": [97.2, 97.0, 96.8, 96.7, 96.6, 96.5, 96.5],
    "delivery_rate": [91.0, 90.5, 89.5, 89.0, 88.5, 88.2, 88.0],
    "andon_response_time": [6.5, 7.0, 7.2, 7.5, 7.8, 7.9, 8.0],
}


async def query_kpi_targets(domain: Optional[str] = None) -> Dict[str, Any]:
    """查询 KPI 目标值"""
    log.info(f"[Monitor] 查询 KPI 目标, 领域: {domain}")
    if MES_API_ENABLED:
        cmd = ["monitor", "targets"]
        if domain:
            cmd.extend(["--domain", domain])
        result = cli_or_mock(cmd, None, True)
        if isinstance(result, dict):
            return result

    if domain:
        filtered = {k: v for k, v in MANUFACTURING_KPIS.items() if v["domain"] == domain}
    else:
        filtered = MANUFACTURING_KPIS
    return {"targets": filtered, "count": len(filtered)}


async def query_kpi_actuals(domain: Optional[str] = None) -> Dict[str, Any]:
    """查询 KPI 实际值"""
    log.info(f"[Monitor] 查询 KPI 实际值, 领域: {domain}")
    if MES_API_ENABLED:
        cmd = ["monitor", "actuals"]
        if domain:
            cmd.extend(["--domain", domain])
        result = cli_or_mock(cmd, None, True)
        if isinstance(result, dict):
            return result

    if domain:
        kpi_keys = [k for k, v in MANUFACTURING_KPIS.items() if v["domain"] == domain]
        actuals = {k: MOCK_KPI_ACTUALS.get(k) for k in kpi_keys if k in MOCK_KPI_ACTUALS}
    else:
        actuals = dict(MOCK_KPI_ACTUALS)
    return {"actuals": actuals, "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M")}


async def query_kpi_summary(domain: Optional[str] = None) -> Dict[str, Any]:
    """查询 KPI 目标 vs 实际对比摘要"""
    log.info(f"[Monitor] 查询 KPI 对比, 领域: {domain}")
    targets = await query_kpi_targets(domain)
    actuals = await query_kpi_actuals(domain)

    items = []
    on_track = 0
    warning = 0
    critical = 0

    target_dict = targets.get("targets", {})
    actual_dict = actuals.get("actuals", {})

    for kpi_key, kpi_def in target_dict.items():
        actual = actual_dict.get(kpi_key)
        if actual is None:
            continue
        status = get_kpi_status(kpi_key, actual)
        gap = actual - kpi_def["target"]
        gap_pct = round((gap / kpi_def["target"]) * 100, 1) if kpi_def["target"] else 0

        item = {
            "key": kpi_key,
            "name": kpi_def["name"],
            "target": kpi_def["target"],
            "actual": actual,
            "unit": kpi_def["unit"],
            "status": status,
            "gap": round(gap, 2),
            "gap_pct": gap_pct,
            "direction": kpi_def["direction"],
        }
        items.append(item)

        if status == "on_track":
            on_track += 1
        elif status == "warning":
            warning += 1
        else:
            critical += 1

    return {
        "items": items,
        "total": len(items),
        "on_track": on_track,
        "warning": warning,
        "critical": critical,
        "fetched_at": actuals.get("fetched_at", ""),
    }


async def query_kpi_trend(kpi_key: str) -> Dict[str, Any]:
    """查询 KPI 趋势数据（最近 7 个周期）"""
    log.info(f"[Monitor] 查询 KPI 趋势, 指标: {kpi_key}")
    if MES_API_ENABLED:
        cmd = ["monitor", "trend", "--kpi", kpi_key]
        result = cli_or_mock(cmd, None, True)
        if isinstance(result, dict) and result:
            return result

    trend = MOCK_KPI_TREND.get(kpi_key, [])
    kpi_def = MANUFACTURING_KPIS.get(kpi_key)

    if not trend or not kpi_def:
        return {"kpi_key": kpi_key, "trend": trend, "error": "无数据"}

    target = kpi_def["target"]
    direction = kpi_def["direction"]
    status = get_kpi_status(kpi_key, trend[-1])
    trend_direction = "improving" if (
        (direction == "higher_better" and trend[-1] >= trend[0]) or
        (direction == "lower_better" and trend[-1] <= trend[0])
    ) else "declining"

    return {
        "kpi_key": kpi_key,
        "kpi_name": kpi_def["name"],
        "target": target,
        "unit": kpi_def["unit"],
        "trend": trend,
        "latest_value": trend[-1],
        "status": status,
        "trend_direction": trend_direction,
    }


def format_goal_report(data: Dict[str, Any]) -> str:
    """格式化 KPI 目标 vs 实际报告"""
    items = data.get("items", [])
    if not items:
        return "暂无 KPI 数据。"

    lines = [
        "## KPI 目标达成报告",
        f"**采集时间**: {data.get('fetched_at', 'N/A')}",
        f"**整体状态**: ✅ 达标 {data.get('on_track', 0)} 项 | ⚠️ 预警 {data.get('warning', 0)} 项 | 🔴 不达标 {data.get('critical', 0)} 项",
        "",
        "| KPI 指标 | 目标值 | 实际值 | 偏差 | 状态 |",
        "|----------|--------|--------|------|------|",
    ]

    status_icon = {"on_track": "✅", "warning": "⚠️", "critical": "🔴"}

    for item in items:
        icon = status_icon.get(item["status"], "⚪")
        gap_str = f"{item['gap']:+.1f}{item['unit']}" if item["unit"] == "%" else f"{item['gap']:+.1f} {item['unit']}"
        lines.append(
            f"| {item['name']} | {item['target']}{item['unit']} | "
            f"{item['actual']}{item['unit']} | {gap_str} | {icon} {item['status']} |"
        )

    # 不达标项汇总
    critical_items = [i for i in items if i["status"] == "critical"]
    if critical_items:
        lines.append("\n### 🔴 重点关注")
        for item in critical_items:
            direction_word = "低于" if item["direction"] == "higher_better" else "高于"
            lines.append(f"- **{item['name']}**: 实际 {item['actual']}{item['unit']}，{direction_word}目标 {item['target']}{item['unit']}（偏差 {item['gap_pct']}%）")

    return "\n".join(lines)


def format_trend_report(data: Dict[str, Any]) -> str:
    """格式化 KPI 趋势报告"""
    if "error" in data:
        return f"趋势数据不可用: {data['error']}"

    trend = data.get("trend", [])
    if not trend:
        return "无趋势数据。"

    arrow = "📈" if data["trend_direction"] == "improving" else "📉"
    status_icon = {"on_track": "✅", "warning": "⚠️", "critical": "🔴"}

    lines = [
        f"## {data['kpi_name']} 趋势 {arrow}",
        f"**目标**: {data['target']}{data['unit']} | **当前**: {data['latest_value']}{data['unit']} | **状态**: {status_icon.get(data['status'], '⚪')} {data['status']}",
        f"**趋势**: {arrow} {'改善中' if data['trend_direction'] == 'improving' else '恶化中'}",
        "",
        "```echarts",
        "{",
        f'  "title": {{"text": "{data["kpi_name"]} 趋势"}},',
        '  "xAxis": {"type": "category", "data": ["D-6", "D-5", "D-4", "D-3", "D-2", "D-1", "今天"]},',
        '  "yAxis": {"type": "value"},',
        f'  "series": [{{"type": "line", "data": {trend}, "markLine": {{"data": [{{"yAxis": {data["target"]}, "label": {{"formatter": "目标: {data["target"]}"}}}}]}}}}]',
        "}",
        "```",
    ]
    return "\n".join(lines)
