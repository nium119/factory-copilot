"""工艺 Agent"""
from typing import Optional, Dict, Any, AsyncGenerator, List
from app.agents.base import BaseAgent
from app.agents.entity_extractor import extract_entities
from app.agents.tools.process_tools import query_process_route, query_process_params, suggest_optimization, format_process
from app.core.logger import log
from app.core.prompts import PROCESS_SYSTEM_PROMPT
from app.services.llm_service import llm_service


class ProcessAgent(BaseAgent):
    name = "process"
    display_name = "工艺助手"
    icon = "🔧"
    color = "#e84393"
    description = "工艺路线查询、参数管理与工艺优化"
    keywords = ["工艺", "流程", "SOP", "工序", "参数", "工艺路线", "BOM", "工艺卡", "操作规范", "工艺优化"]
    system_prompt = PROCESS_SYSTEM_PROMPT

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
        entities = await extract_entities(message, domain="process")
        product = entities.get("product")

        if "优化" in message or "良率" in message:
            return await suggest_optimization(product)
        if "参数" in message:
            params = await query_process_params(product)
            parts = ["## 工艺参数\n"]
            for step, p in params.items():
                parts.append(f"**{step}**: " + ", ".join([f"{k}:{v}" for k, v in p.items()]))
            return "\n".join(parts)
        if "工艺路线" in message or "路线" in message:
            routes = await query_process_route()
            return format_process(routes)
        return None


process_agent = ProcessAgent()
