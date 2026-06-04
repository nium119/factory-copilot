"""质量设备 Agent — 质检 + 设备管理"""
from typing import Optional

from app.agents.base import BaseAgent
from app.core.prompts import QUALITY_EQUIPMENT_SYSTEM_PROMPT


class QualityEquipmentAgent(BaseAgent):
    name = "quality_equipment"
    display_name = "质量设备"
    icon = "⚙️"
    color = "#e17055"
    description = "质量设备助手：质检分析、缺陷诊断、设备状态、故障维修、OEE监控"

    system_prompt = QUALITY_EQUIPMENT_SYSTEM_PROMPT

    async def call_tools(self, message: str) -> Optional[str]:
        return await self._call_tools_via_ontology(message)


quality_equipment_agent = QualityEquipmentAgent()
