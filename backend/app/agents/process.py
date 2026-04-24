"""工艺 Agent"""
from typing import Optional, Dict, Any, AsyncGenerator, List
from app.agents.base import BaseAgent
from app.agents.agent_config import AGENT_DEFINITIONS
from app.agents.entity_extractor import extract_entities
from app.agents.tools.process_tools import query_process_route, query_process_params, suggest_optimization, format_process
from app.core.logger import log
from app.core.prompts import PROCESS_SYSTEM_PROMPT
from app.services.llm_service import llm_service


class ProcessAgent(BaseAgent):
    _meta = AGENT_DEFINITIONS["process"]
    name = "process"
    display_name = _meta["display_name"]
    icon = _meta["icon"]
    color = _meta["color"]
    description = _meta["description"]
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
        matched_agents: Optional[List[str]] = None,
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
            return f"工艺参数: {', '.join(f'{k}={v}' for k, v in params.items())}" if params else "未找到对应工艺参数。"
        if "工艺" in message or "路线" in message or "流程" in message:
            routes = await query_process_route(product)
            return format_process(routes)
        return None


process_agent = ProcessAgent()
