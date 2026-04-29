"""库存 Agent"""
from typing import Optional, List
from app.agents.base import BaseAgent
from app.agents.entity_extractor import extract_entities
from app.agents.tools.inventory_tools import query_inventory, query_inventory_summary, check_shortage, format_inventory
from app.core.logger import log
from app.core.prompts import INVENTORY_SYSTEM_PROMPT


class InventoryAgent(BaseAgent):
    name = "inventory"
    system_prompt = INVENTORY_SYSTEM_PROMPT

    async def call_tools(self, message: str) -> Optional[str]:
        entities = await extract_entities(message, domain="inventory")
        product = entities.get("product")

        if "概况" in message or "汇总" in message:
            summary = await query_inventory_summary()
            return f"库存概况: 总物料 {summary['total_items']} 种，充足 {summary['sufficient']} 种，预警 {summary['warning']} 种，缺料 {summary['shortage']} 种"
        if "缺料" in message or "预警" in message or "不足" in message:
            shortages = await check_shortage()
            if shortages:
                lines = ["## 缺料预警\n"]
                for s in shortages:
                    lines.append(f"  [!] {s['name']}({s['sku']}): 库存 {s['stock']} {s['unit']} (安全 {s['safety_stock']})")
                return "\n".join(lines)
            return "暂无缺料预警。"
        if any(k in message for k in ["库存", "物料", "查询"]):
            inv = await query_inventory(product)
            return format_inventory(inv)
        return None


inventory_agent = InventoryAgent()
