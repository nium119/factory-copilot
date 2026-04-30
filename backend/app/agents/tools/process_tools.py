"""工艺工具 — 模拟数据 + MES CLI 接入"""
import os
from typing import Any, Dict, List, Optional

from app.agents.tools.mes_cli_runner import cli_or_mock
from app.core.logger import log

MES_API_ENABLED = os.getenv("MES_API_ENABLED", "false").lower() == "true"

MOCK_PROCESS_ROUTES = [
    {"product": "主板A", "route": "上料 → 锡膏印刷 → SPI → 贴片 → 回流焊 → AOI → 功能测试 → 包装", "steps": 8, "cycle_time": "120s", "yield_rate": 96.5},
    {"product": "控制板B", "route": "上料 → 锡膏印刷 → 贴片 → 回流焊 → AOI → 功能测试 → 包装", "steps": 7, "cycle_time": "95s", "yield_rate": 99.3},
    {"product": "电源模块C", "route": "上料 → 锡膏印刷 → SPI → 贴片(DIP) → 波峰焊 → 手工补焊 → 功能测试 → 包装", "steps": 8, "cycle_time": "150s", "yield_rate": 92.9},
]

MOCK_PROCESS_PARAMS = {
    "回流焊": {"预热区": "150-180°C", "恒温区": "180-220°C", "回流区": "235-245°C", "冷却区": "自然冷却", "传送速度": "80cm/min"},
    "波峰焊": {"预热": "100-130°C", "波峰温度": "260-270°C", "传送角度": "6°", "传送速度": "1.2m/min"},
    "锡膏印刷": {"刮刀压力": "3-5kg", "刮刀速度": "40-60mm/s", "脱模速度": "1-3mm/s", "印刷间隙": "0-0.1mm"},
}


async def query_process_route(product: Optional[str] = None) -> List[Dict[str, Any]]:
    """查询工艺路线"""
    log.info(f"[工艺工具] 查询工艺路线, 产品: {product}")
    cmd = ["process", "route"]
    if product:
        cmd.extend(["--product", product])
    result = cli_or_mock(cmd, MOCK_PROCESS_ROUTES, MES_API_ENABLED)
    if isinstance(result, list):
        if product:
            return [r for r in result if product.lower() in r.get("product", "").lower()]
        return result
    return MOCK_PROCESS_ROUTES


async def query_process_params(step: Optional[str] = None, product: Optional[str] = None) -> Dict[str, Any]:
    """查询工艺参数"""
    log.info(f"[工艺工具] 查询工艺参数, 工序: {step}, 产品: {product}")
    cmd = ["process", "params"]
    if step:
        cmd.extend(["--process", step])
    if product:
        cmd.extend(["--product", product])
    result = cli_or_mock(cmd, MOCK_PROCESS_PARAMS, MES_API_ENABLED)
    if isinstance(result, dict):
        if step:
            return {k: v for k, v in result.items() if step.lower() in k.lower()}
        return result
    return MOCK_PROCESS_PARAMS


async def suggest_optimization(product: str = "", issue: Optional[str] = None) -> str:
    """工艺优化建议"""
    log.info(f"[工艺工具] 工艺优化建议, 产品: {product}, 问题: {issue}")
    if MES_API_ENABLED:
        cmd = ["process", "optimize"]
        if product:
            cmd.extend(["--product", product])
        if issue:
            cmd.extend(["--issue", issue])
        data = cli_or_mock(cmd, None, True)
        if isinstance(data, dict):
            return str(data)
    lines = ["## 工艺优化建议\n"]
    for r in MOCK_PROCESS_ROUTES:
        if r["yield_rate"] < 97:
            lines.append(f"**{r['product']}** (当前良率: {r['yield_rate']}%)")
            if r["yield_rate"] < 93:
                lines.append("  - 良率偏低，建议:")
                lines.append("    1. 检查波峰焊参数（温度/传送速度/角度）")
                lines.append("    2. 优化DIP插件SOP，减少人工失误")
                lines.append("    3. 增加SPI检测覆盖率")
            else:
                lines.append("  - 良率可进一步提升，建议:")
                lines.append("    1. 优化回流焊温度曲线")
                lines.append("    2. 加强AOI检测参数校准")
            lines.append("")
    if not lines[1:]:
        lines.append("所有产品工艺路线运行良好，无需优化。")
    return "\n".join(lines)


def format_process(routes: List[Dict[str, Any]]) -> str:
    """格式化工艺数据为文本"""
    if not routes:
        return "无工艺路线数据。"
    lines = ["## 工艺路线\n"]
    for r in routes:
        lines.append(f"**{r['product']}** (CT: {r['cycle_time']}, 良率: {r['yield_rate']}%, {r['steps']} 步)")
        lines.append(f"  流程: {r['route']}\n")
    return "\n".join(lines)
