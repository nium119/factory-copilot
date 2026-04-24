"""排产 Agent"""
from typing import Optional, Dict, Any, AsyncGenerator, List
from app.agents.base import BaseAgent
from app.agents.entity_extractor import extract_entities
from app.agents.tools.scheduling_tools import query_schedule, query_capacity, suggest_schedule, format_schedule
from app.core.logger import log
from app.core.prompts import SCHEDULING_SYSTEM_PROMPT
from app.services.llm_service import llm_service


class SchedulingAgent(BaseAgent):
    name = "scheduling"
    display_name = "排产助手"
    icon = "📋"
    color = "#0984e3"
    description = "生产计划排期、产能分析与调度优化"
    keywords = ["排产", "排期", "计划", "调度", "排班", "产线安排", "生产计划", "工单排程", "产能"]
    system_prompt = SCHEDULING_SYSTEM_PROMPT

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
        entities = await extract_entities(message, domain="scheduling")
        line = entities.get("line")

        if any(k in message for k in ["排产", "计划", "产线", "工单"]):
            schedules = await query_schedule(line)
            return format_schedule(schedules)
        if "产能" in message:
            cap = await query_capacity()
            return f"今日产能: 目标 {cap['today_target']} 件，实际 {cap['today_actual']} 件，完成率 {cap['overall_progress']}%，运行产线 {cap['active_lines']}/{cap['total_lines']} 条"
        if "建议" in message:
            product = entities.get("product", "")
            urgency = entities.get("urgency", "normal")
            return await suggest_schedule(product=product, urgency=urgency)
        return None


scheduling_agent = SchedulingAgent()
