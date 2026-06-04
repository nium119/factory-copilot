"""生产管理 Agent — 排产 + 工艺 + 库存"""
from typing import Optional

from app.agents.base import BaseAgent
from app.core.prompts import PRODUCTION_MANAGEMENT_SYSTEM_PROMPT


class ProductionManagementAgent(BaseAgent):
    name = "production_management"
    display_name = "生产管理"
    icon = "📋"
    color = "#0984e3"
    description = "生产管理助手：排产调度、工艺路线、物料库存、产能分析"

    system_prompt = PRODUCTION_MANAGEMENT_SYSTEM_PROMPT

    async def call_tools(self, message: str) -> Optional[str]:
        return await self._call_tools_via_ontology(message)


production_management_agent = ProductionManagementAgent()
