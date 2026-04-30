"""工艺 Agent"""
from typing import Optional

from app.agents.base import BaseAgent
from app.agents.entity_extractor import extract_entities
from app.agents.tools.process_tools import (
    format_process,
    query_process_params,
    query_process_route,
    suggest_optimization,
)
from app.core.prompts import PROCESS_SYSTEM_PROMPT


class ProcessAgent(BaseAgent):
    name = "process"
    system_prompt = PROCESS_SYSTEM_PROMPT

    async def call_tools(self, message: str) -> Optional[str]:
        entities = await extract_entities(message, domain="process")
        product = entities.get("product")

        if "优化" in message or "良率" in message:
            return await suggest_optimization(product)
        if "参数" in message:
            params = await query_process_params(product)
            return f"工艺参数: {', '.join(f'{k}={v}' for k, v in params.items())}" if params else "未找到对应工艺参数。"
        if "工艺" in message or "路线" in message or "流程" in message:
            routes = await query_process_route(product)
            return format_process(routes)
        return None


process_agent = ProcessAgent()
