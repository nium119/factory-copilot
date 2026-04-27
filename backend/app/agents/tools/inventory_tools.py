"""库存工具 — 模拟数据 + MES CLI 接入"""
import os
from typing import Dict, Any, Optional, List
from app.core.logger import log
from app.agents.tools.mes_cli_runner import cli_or_mock

MES_API_ENABLED = os.getenv("MES_API_ENABLED", "false").lower() == "true"

MOCK_INVENTORY = [
    {"name": "0402电阻 1KΩ", "sku": "R-0402-1K", "stock": 50000, "unit": "pcs", "safety_stock": 10000, "status": "充足"},
    {"name": "0402电容 100nF", "sku": "C-0402-100N", "stock": 45000, "unit": "pcs", "safety_stock": 10000, "status": "充足"},
    {"name": "STM32F103C8T6", "sku": "IC-MCU-STM32", "stock": 1200, "unit": "pcs", "safety_stock": 500, "status": "正常"},
    {"name": "锡膏 SAC305", "sku": "PASTE-SAC305", "stock": 8, "unit": "瓶", "safety_stock": 10, "status": "预警"},
    {"name": "PCB主板A (未贴装)", "sku": "PCB-MAIN-A", "stock": 200, "unit": "pcs", "safety_stock": 300, "status": "缺料"},
    {"name": "连接器 2.54mm 10P", "sku": "CONN-2.54-10P", "stock": 15000, "unit": "pcs", "safety_stock": 3000, "status": "充足"},
]

MOCK_INVENTORY_SUMMARY = {
    "total_items": 6,
    "sufficient": 3,
    "normal": 1,
    "warning": 1,
    "shortage": 1,
    "total_value": "约 ¥125,000",
}


async def query_inventory(keyword: Optional[str] = None, warehouse: Optional[str] = None) -> List[Dict[str, Any]]:
    """查询库存"""
    log.info(f"[库存工具] 查询库存, 关键词: {keyword}, 仓库: {warehouse}")
    cmd = ["inventory", "query"]
    if keyword:
        cmd.extend(["--material", keyword])
    if warehouse:
        cmd.extend(["--warehouse", warehouse])
    result = cli_or_mock(cmd, MOCK_INVENTORY, MES_API_ENABLED)
    if isinstance(result, list):
        if keyword:
            return [i for i in result if keyword.lower() in i.get("name", "").lower() or keyword.lower() in i.get("sku", "").lower()]
        return result
    return MOCK_INVENTORY


async def query_inventory_summary() -> Dict[str, Any]:
    """查询库存概况"""
    return cli_or_mock(["inventory", "summary"], MOCK_INVENTORY_SUMMARY, MES_API_ENABLED)


async def check_shortage() -> str:
    """缺料预警"""
    log.info("[库存工具] 查询缺料预警")
    if MES_API_ENABLED:
        data = cli_or_mock(["inventory", "shortage"], None, True)
        if isinstance(data, list):
            shortage = [i for i in data if i.get("status") in ("预警", "缺料")]
        else:
            shortage = []
    else:
        shortage = [i for i in MOCK_INVENTORY if i["status"] in ("预警", "缺料")]
    if not shortage:
        return "当前无缺料预警。"
    lines = ["## 缺料预警\n"]
    for i in shortage:
        status_icon = "⚠️" if i["status"] == "预警" else "🔴"
        lines.append(f"{status_icon} **{i['name']}** ({i['sku']})")
        lines.append(f"  当前库存: {i['stock']} {i['unit']} / 安全库存: {i['safety_stock']} {i['unit']}")
        if i["status"] == "缺料":
            gap = i["safety_stock"] - i["stock"]
            lines.append(f"  **缺 {gap} {i['unit']}**，建议立即采购")
        else:
            gap = i["safety_stock"] - i["stock"]
            lines.append(f"  低于安全库存 {gap} {i['unit']}，建议尽快补充")
        lines.append("")
    return "\n".join(lines)


def format_inventory(items: List[Dict[str, Any]]) -> str:
    """格式化库存数据为文本"""
    if not items:
        return "无库存数据。"
    lines = ["## 库存明细\n"]
    lines.append("| 物料 | SKU | 库存 | 单位 | 安全库存 | 状态 |")
    lines.append("|------|-----|------|------|----------|------|")
    for i in items:
        lines.append(f"| {i['name']} | {i['sku']} | {i['stock']} | {i['unit']} | {i['safety_stock']} | {i['status']} |")
    return "\n".join(lines)
