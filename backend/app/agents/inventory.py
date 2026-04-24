"""库存 Agent"""
from typing import Optional, Dict, Any, AsyncGenerator, List
from app.agents.base import BaseAgent
from app.agents.entity_extractor import extract_entities
from app.agents.tools.inventory_tools import query_inventory, query_inventory_summary, check_shortage, format_inventory
from app.core.logger import log
from app.core.prompts import INVENTORY_SYSTEM_PROMPT
from app.services.llm_service import llm_service


class InventoryAgent(BaseAgent):
    name = "inventory"
    display_name = "线边仓助手"
    icon = "📦"
    color = "#00b894"
    description = "线边仓管理，支持库存查询、缺料预警与物料规划"
    keywords = ["库存", "物料", "仓库", "缺料", "盘点", "出入库", "备料", "发料", "领料", "物料状态", "线边仓"]
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

        if "缺料" in message or "预警" in message:
            return await check_shortage(product)
        if "概况" in message:
            summary = await query_inventory_summary()
            return f"库存概况: 共 {summary['total_items']} 类物料，充足 {summary['sufficient']} 项，正常 {summary['normal']} 项，预警 {summary['warning']} 项，缺料 {summary['shortage']} 项，总价值 {summary['total_value']}"
        if any(k in message for k in ["物料", "查询", "库存", "详情"]):
            data = await query_inventory(product)
            return format_inventory(data)
        return None


inventory_agent = InventoryAgent()
