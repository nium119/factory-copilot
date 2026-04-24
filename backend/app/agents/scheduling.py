"""排产 Agent"""
from typing import Optional, Dict, Any, AsyncGenerator, List
from app.agents.base import BaseAgent
from app.agents.agent_config import AGENT_DEFINITIONS
from app.agents.settings import REFLECTION_ACTIONABLE_KEYWORDS
from app.agents.entity_extractor import extract_entities
import json
from app.agents.tools.scheduling_tools import query_schedule, query_capacity, suggest_schedule, format_schedule, optimize_schedule, format_schedule_optimization
from app.core.logger import log
from app.core.prompts import SCHEDULING_SYSTEM_PROMPT
from app.services.llm_service import llm_service


class SchedulingAgent(BaseAgent):
    _meta = AGENT_DEFINITIONS["scheduling"]
    name = "scheduling"
    display_name = _meta["display_name"]
    icon = _meta["icon"]
    color = _meta["color"]
    description = _meta["description"]
    system_prompt = SCHEDULING_SYSTEM_PROMPT

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
        tool_result, eval_data = await self.call_tools(message)
        enhanced = f"{message}\n\n参考数据:\n{tool_result}" if tool_result else message
        if eval_data:
            yield ('eval_result', eval_data)
        async for t, c in llm_service.chat_stream(
            message=enhanced, session_id=session_id,
            system_prompt=context.get("system_prompt", self.system_prompt) if context else self.system_prompt,
            model_name=model_name,
            use_agent=use_agent, web_search=web_search,
            history_messages=history_messages,
            enable_thinking=enable_thinking,
        ):
            yield t, c

    async def call_tools(self, message: str) -> tuple:
        """返回 (格式化文本, 评估数据或None)"""
        entities = await extract_entities(message, domain="scheduling")
        line = entities.get("line")

        if "优化" in message:
            result = await optimize_schedule()
            eval_info = {
                "scores": result["evaluation"]["scores"],
                "overall_score": result["evaluation"]["overall_score"],
                "max_score": result["evaluation"]["max_score"],
                "needs_optimization": result["evaluation"]["needs_optimization"],
                "suggestions": result["evaluation"]["suggestions"],
            }
            return format_schedule_optimization(result), eval_info
        if any(k in message for k in ["排产", "计划", "产线", "工单"]):
            schedules = await query_schedule(line)
            return format_schedule(schedules), None
        if "产能" in message:
            cap = await query_capacity()
            return f"今日产能: 目标 {cap['today_target']} 件，实际 {cap['today_actual']} 件，完成率 {cap['overall_progress']}%，运行产线 {cap['active_lines']}/{cap['total_lines']} 条", None
        if "建议" in message:
            product = entities.get("product", "")
            urgency = entities.get("urgency", "normal")
            return await suggest_schedule(product=product, urgency=urgency), None
        return None, None

    async def reflect(self, message: str, response: str) -> Optional[str]:
        """排产响应自检规则"""
        sched_ref = REFLECTION_ACTIONABLE_KEYWORDS["scheduling"]
        issues = []

        if any(k in message for k in ["排产", "计划", "产线"]):
            if response and not any(k in response for k in sched_ref["response_check"]):
                issues.append("排产响应应包含具体的产线或工单信息")

        if "产能" in message and response:
            if "目标" not in response and "实际" not in response:
                issues.append("产能分析应包含目标值与实际值对比")

        if "建议" in message and response:
            if not any(k in response for k in sched_ref["suggestion_check"]):
                issues.append("排产建议应给出明确的推荐方案")

        if issues:
            log.info(f"[SchedulingAgent] 反思发现问题，触发 LLM 修正: {issues}")
            fix_prompt = (
                f"你的上一个响应存在以下不足：{'；'.join(issues)}。\n\n"
                f"用户原始问题：{message}\n\n"
                f"你之前的回答：{response}\n\n"
                f"请重新生成一份更完整的回答。"
            )
            try:
                result = await llm_service.chat_sync(
                    message=fix_prompt, session_id="reflection", system_prompt=self.system_prompt,
                )
                return result
            except Exception as e:
                log.warning(f"[SchedulingAgent] LLM 修正失败: {e}")
        return None


scheduling_agent = SchedulingAgent()
