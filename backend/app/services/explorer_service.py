"""探索与发现 — 定时任务分析生产数据，发现异常主动推送"""
from datetime import datetime
from typing import Any, Dict, List

from app.core.logger import log

# 模拟数据源
EXPLORATION_DATA_SOURCES = {
    "quality": "质检良率趋势",
    "equipment": "设备OEE趋势",
    "production": "产线产出趋势",
    "andon": "安灯异常统计",
    "inventory": "库存预警",
}


async def analyze_production_data(hours: int = 24) -> Dict[str, Any]:
    """
    分析生产数据，发现异常并主动推送

    Returns:
        分析结果 + 异常告警列表
    """
    log.info(f"[Explorer] 分析生产数据，最近 {hours} 小时")

    anomalies = []

    # 1. 质量趋势分析
    quality_issues = _check_quality_trend()
    if quality_issues:
        anomalies.extend(quality_issues)

    # 2. 设备异常分析
    equipment_issues = _check_equipment_anomaly()
    if equipment_issues:
        anomalies.extend(equipment_issues)

    # 3. 产出趋势分析
    production_issues = _check_production_trend()
    if production_issues:
        anomalies.extend(production_issues)

    # 4. 安灯趋势分析
    andon_issues = _check_andon_trend()
    if andon_issues:
        anomalies.extend(andon_issues)

    return {
        "analyzed_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "analysis_period_hours": hours,
        "anomaly_count": len(anomalies),
        "anomalies": anomalies,
        "summary": _generate_summary(anomalies),
    }


def _check_quality_trend() -> List[Dict[str, Any]]:
    """质量趋势分析"""
    # 模拟：DIP-01 连锡不良率连续 3 小时 > 3%
    return [
        {
            "source": "quality",
            "severity": "high",
            "title": "DIP-01 连锡不良率持续偏高",
            "description": "最近 3 小时连锡不良率均在 3.2%-3.8% 之间，超过 3% 的告警阈值",
            "suggestion": "建议检查波峰焊温度和链速参数",
        },
    ]


def _check_equipment_anomaly() -> List[Dict[str, Any]]:
    """设备异常分析"""
    # 模拟：SMT-02 OEE 低于 70%
    return [
        {
            "source": "equipment",
            "severity": "medium",
            "title": "SMT-02 设备 OEE 偏低",
            "description": "SMT-02 今日 OEE 仅 68%，低于目标 80%",
            "suggestion": "建议安排设备维护检查",
        },
    ]


def _check_production_trend() -> List[Dict[str, Any]]:
    """产出趋势分析"""
    return []  # 暂无产出异常


def _check_andon_trend() -> List[Dict[str, Any]]:
    """安灯趋势分析"""
    return [
        {
            "source": "andon",
            "severity": "medium",
            "title": "SMT-01 安灯呼叫频率偏高",
            "description": "今日 SMT-01 已触发 3 次安灯呼叫，高于日均 1.5 次",
            "suggestion": "建议安排设备巡检，提前排查隐患",
        },
    ]


def _generate_summary(anomalies: List[Dict[str, Any]]) -> str:
    """生成探索摘要"""
    if not anomalies:
        return "未发现异常。"

    high_count = sum(1 for a in anomalies if a["severity"] == "high")
    medium_count = sum(1 for a in anomalies if a["severity"] == "medium")
    low_count = sum(1 for a in anomalies if a["severity"] == "low")

    parts = [f"共发现 {len(anomalies)} 项异常"]
    if high_count:
        parts.append(f"高优先级 {high_count} 项")
    if medium_count:
        parts.append(f"中优先级 {medium_count} 项")
    if low_count:
        parts.append(f"低优先级 {low_count} 项")

    return "，".join(parts) + "。"


def format_explorer_report(data: Dict[str, Any]) -> str:
    """格式化探索报告"""
    lines = ["## 生产数据探索报告\n"]
    lines.append(f"**分析时间**: {data['analyzed_at']}")
    lines.append(f"**分析范围**: 最近 {data['analysis_period_hours']} 小时\n")
    lines.append(f"**摘要**: {data['summary']}\n")

    if data["anomalies"]:
        for i, anomaly in enumerate(data["anomalies"], 1):
            severity_icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(anomaly["severity"], "⚪")
            lines.append(f"### {i}. {severity_icon} {anomaly['title']}")
            lines.append(f"**来源**: {anomaly['source']} | **优先级**: {anomaly['severity']}")
            lines.append(f"**描述**: {anomaly['description']}")
            lines.append(f"**建议**: {anomaly['suggestion']}")
            lines.append("")
    else:
        lines.append("未发现异常，生产运行正常。")

    return "\n".join(lines)
