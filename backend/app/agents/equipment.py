"""设备 Agent"""
from typing import Optional

from app.agents.base import BaseAgent
from app.agents.entity_extractor import extract_entities
from app.agents.settings import COLLAB_DISPLAY_LIMITS
from app.agents.tools.equipment_tools import diagnose_fault, format_equipment, query_equipment, query_equipment_summary
from app.agents.tools.inventory_tools import query_inventory
from app.agents.tools.scheduling_tools import query_schedule
from app.core.prompts import EQUIPMENT_SYSTEM_PROMPT


class EquipmentAgent(BaseAgent):
    name = "equipment"
    system_prompt = EQUIPMENT_SYSTEM_PROMPT

    def _get_reasoning_framework(self, message: str) -> str:
        if "故障" in message or "诊断" in message or "影响" in message:
            from app.services.ontology_service import ontology_service
            return ontology_service.get_domain_knowledge().get("equipment_diagnosis", "")
        return ""

    async def call_tools(self, message: str) -> Optional[str]:
        entities = await extract_entities(message, domain="equipment")
        line = entities.get("line")
        shortage_statuses = ("预警", "缺料")
        max_schedule_items = COLLAB_DISPLAY_LIMITS["max_schedule_items"]

        if "故障" in message or "诊断" in message or "影响" in message:
            fault_diag = await diagnose_fault(line)
            inv_results = await query_inventory()
            shortage_items = [i for i in (inv_results or []) if i.get("status") in shortage_statuses]
            sched_results = await query_schedule(line)

            lines = [fault_diag]
            if shortage_items:
                lines.append("\n### 备件库存预警")
                for item in shortage_items:
                    lines.append(f"  [!] {item['name']}: 库存 {item['stock']} {item['unit']} (安全 {item['safety_stock']})")
            if sched_results:
                lines.append("\n### 受影响排产")
                if isinstance(sched_results, list):
                    for s in sched_results[:max_schedule_items]:
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
