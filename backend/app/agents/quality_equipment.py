"""质量设备 Agent — 质检 + 设备管理

@deprecated: 由 GenericAgent + 编译器替代。保留作为编译模式不可用时的回退。
"""
from typing import Optional

from app.agents.base import BaseAgent


def _get_system_prompt():
    """编译模式下用编译器产出，否则回退旧 prompt。"""
    try:
        from app.agents import get_compiled_runtime
        runtime = get_compiled_runtime()
        if runtime:
            for ad in runtime.agents:
                if ad.name == "quality_equipment":
                    return ad.system_prompt
    except Exception:
        pass
    from app.core.prompts import QUALITY_EQUIPMENT_SYSTEM_PROMPT
    return QUALITY_EQUIPMENT_SYSTEM_PROMPT


class QualityEquipmentAgent(BaseAgent):
    name = "quality_equipment"
    display_name = "质量设备"
    icon = "⚙️"
    color = "#e17055"
    description = "质量设备助手：质检分析、缺陷诊断、设备状态、故障维修、OEE监控"

    system_prompt = _get_system_prompt()

    async def call_tools(self, message: str) -> Optional[str]:
        return await self._call_tools_via_ontology(message)


quality_equipment_agent = QualityEquipmentAgent()
