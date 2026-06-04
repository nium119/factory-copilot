"""生产执行 Agent — 工位操作 + 安灯异常 + 生产准备"""
from typing import Optional

from app.agents.base import BaseAgent
from app.core.prompts import PRODUCTION_EXECUTION_SYSTEM_PROMPT


class ProductionExecutionAgent(BaseAgent):
    name = "production_execution"
    display_name = "生产执行"
    icon = "🖥️"
    color = "#45aaf2"
    description = "产线操作执行助手：工位报工、安灯异常、生产准备检查、SOP查看、物料领用"

    system_prompt = PRODUCTION_EXECUTION_SYSTEM_PROMPT

    async def call_tools(self, message: str) -> Optional[str]:
        return await self._call_tools_via_ontology(message)


production_execution_agent = ProductionExecutionAgent()
