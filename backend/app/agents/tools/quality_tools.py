"""质检工具 — 模拟数据 + 预留 MES API 接入"""
from typing import Dict, Any, Optional, List
from app.core.logger import log

MES_API_BASE = "http://localhost:9090"
MES_API_ENABLED = False

MOCK_QUALITY_REPORTS = [
    {"product": "主板A", "batch": "B20260422-01", "inspected": 380, "passed": 365, "failed": 15, "rate": 96.05, "defects": [{"type": "虚焊", "count": 8}, {"type": "偏移", "count": 5}, {"type": "短路", "count": 2}]},
    {"product": "控制板B", "batch": "B20260422-02", "inspected": 300, "passed": 298, "failed": 2, "rate": 99.33, "defects": [{"type": "缺件", "count": 2}]},
    {"product": "电源模块C", "batch": "B20260422-03", "inspected": 85, "passed": 79, "failed": 6, "rate": 92.94, "defects": [{"type": "极性反", "count": 3}, {"type": "冷焊", "count": 3}]},
]

MOCK_QUALITY_SUMMARY = {
    "total_inspected": 765,
    "total_passed": 742,
    "total_failed": 23,
    "overall_rate": 96.99,
    "target_rate": 98.0,
    "top_defects": [{"type": "虚焊", "count": 8, "rate": 34.8}, {"type": "偏移", "count": 5, "rate": 21.7}, {"type": "极性反", "count": 3, "rate": 13.0}],
}


async def query_quality_report(product: Optional[str] = None) -> List[Dict[str, Any]]:
    """查询质检报告"""
    log.info(f"[质检工具] 查询质检报告, 产品: {product}")
    if product:
        return [r for r in MOCK_QUALITY_REPORTS if product.lower() in r["product"].lower()]
    return MOCK_QUALITY_REPORTS


async def query_quality_summary() -> Dict[str, Any]:
    """查询质检概况"""
    return MOCK_QUALITY_SUMMARY


async def analyze_defects(product: str = "") -> str:
    """缺陷分析"""
    log.info(f"[质检工具] 缺陷分析, 产品: {product}")
    summary = MOCK_QUALITY_SUMMARY
    lines = ["## 缺陷分析\n"]
    lines.append(f"今日共检出不良品 {summary['total_failed']} 件，不良率 {summary['overall_rate']}%")
    if summary["overall_rate"] < summary["target_rate"]:
        lines.append(f"**⚠️ 未达目标**: 目标合格率 {summary['target_rate']}%，当前 {summary['overall_rate']}%\n")
    lines.append("| 缺陷类型 | 数量 | 占比 |")
    lines.append("|----------|------|------|")
    for d in summary["top_defects"]:
        lines.append(f"| {d['type']} | {d['count']} | {d['rate']}% |")
    lines.append("\n**建议**: 重点关注虚焊问题，建议检查回流焊温度曲线和锡膏印刷参数")
    return "\n".join(lines)


def format_quality(reports: List[Dict[str, Any]]) -> str:
    """格式化质检报告为文本"""
    if not reports:
        return "当前无质检报告。"
    lines = ["## 质检报告\n"]
    lines.append("| 产品 | 批次 | 检测数 | 合格 | 不良 | 合格率 |")
    lines.append("|------|------|--------|------|------|--------|")
    for r in reports:
        lines.append(f"| {r['product']} | {r['batch']} | {r['inspected']} | {r['passed']} | {r['failed']} | {r['rate']}% |")
        if r["defects"]:
            defect_list = ", ".join([f"{d['type']}({d['count']})" for d in r["defects"]])
            lines.append(f"  主要缺陷: {defect_list}")
    return "\n".join(lines)
