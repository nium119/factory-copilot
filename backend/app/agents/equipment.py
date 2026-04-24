"""设备 Agent"""
from typing import Optional, Dict, Any, AsyncGenerator, List
from app.agents.base import BaseAgent
from app.agents.entity_extractor import extract_entities
from app.agents.tools.equipment_tools import query_equipment, query_equipment_summary, diagnose_fault, format_equipment
from app.agents.tools.inventory_tools import query_inventory as _query_inventory
from app.agents.tools.scheduling_tools import query_schedule as _query_schedule
from app.core.logger import log
from app.core.prompts import EQUIPMENT_SYSTEM_PROMPT
from app.services.llm_service import llm_service


class EquipmentAgent(BaseAgent):
    name = "equipment"
    display_name = "设备助手"
    icon = "⚙️"
    color = "#fdcb6e"
    description = "设备状态监控、故障诊断与维护计划"
    keywords = ["设备", "故障", "维修", "保养", "停机", "OEE", "开机率", "设备状态", "点检", "巡检", "备件"]
    system_prompt = EQUIPMENT_SYSTEM_PROMPT

    async def process(
        self,
        message: str,
        session_id: str = "default",
        model_name: Optional[str] = None,
        use_agent: bool = False,
        web_search: bool = False,
        enable_thinking: bool = False,
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
        entities = await extract_entities(message, domain="equipment")
        line = entities.get("line")

        if "故障" in message or "诊断" in message or "影响" in message:
            # 跨 Agent 联动：故障诊断 + 备件库存 + 排产影响
            fault_diag = await diagnose_fault(line)
            # 查备件库存（故障设备相关备件）
            inv_results = await _query_inventory()
            shortage_items = [i for i in inv_results if i["status"] in ("预警", "缺料")]
            # 查受影响产线的排产
            sched_results = await _query_schedule(line)

            lines = [fault_diag]
            if shortage_items:
                lines.append("\n### 备件库存预警")
                for item in shortage_items:
                    lines.append(f"  [!] {item['name']}: 库存 {item['stock']} {item['unit']} (安全 {item['safety_stock']})")
            if sched_results:
                lines.append("\n### 受影响排产")
                if isinstance(sched_results, list):
                    for s in sched_results[:5]:
                        lines.append(f"  - {s.get('wo_id', s.get('order_id', 'N/A'))}: {s.get('product', 'N/A')} ({s.get('status', 'N/A')})")
                elif isinstance(sched_results, dict):
                    lines.append(f"  排产数据: {sched_results}")
            return "\n".join(lines)

        if "概况" in message or "OEE" in message:
            summary = await query_equipment_summary()
            return f"设备概况: 共 {summary['total']} 台，运行 {summary['running']} 台，维护中 {summary['maintenance']} 台，停机 {summary['stopped']} 台，运行设备平均 OEE {summary['active_avg_oee']}%"
        if any(k in message for k in ["设备", "状态", "查询", "运行"]):
            equip = await query_equipment(line)
            return format_equipment(equip)
        return None


equipment_agent = EquipmentAgent()
