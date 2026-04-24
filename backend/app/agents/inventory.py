"""库存 Agent"""
from typing import Optional, Dict, Any, AsyncGenerator, List
from app.agents.base import BaseAgent
from app.agents.agent_config import AGENT_DEFINITIONS
from app.agents.entity_extractor import extract_entities
from app.agents.tools.inventory_tools import query_inventory, query_inventory_summary, check_shortage, format_inventory
from app.core.logger import log
from app.core.prompts import INVENTORY_SYSTEM_PROMPT
from app.services.llm_service import llm_service


class InventoryAgent(BaseAgent):
    _meta = AGENT_DEFINITIONS["inventory"]
    name = "inventory"
    display_name = _meta["display_name"]
    icon = _meta["icon"]
    color = _meta["color"]
    description = _meta["description"]
    system_prompt = INVENTORY_SYSTEM_PROMPT

    async def process(
        self,
        message: str,
        session_id: str = "default",
        model_name: Optional[str] = None,
        use_agent: bool = False,
        web_search: bool = False,
        enable_thinking: Optional[bool] = None,
        context: Optional[Dict[str, Any]] = None,
        history_messages: Optional[List] = None,
        matched_agents: Optional[List[str]] = None,
    ) -> AsyncGenerator[tuple, None]:
        tool_result = await self.call_tools(message)
        enhanced = f"{message}\n\n参考数据:\n{tool_result}" if tool_result else message
        async for t, c in llm_service.chat_stream(
            message=enhanced, session_id=session_id,
            system_prompt=context.get("system_prompt", self.system_prompt) if context else self.system_prompt,
            model_name=model_name,
            use_agent=use_agent, web_search=web_search,
            history_messages=history_messages,
            enable_thinking=enable_thinking,
        ):
            yield t, c

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
