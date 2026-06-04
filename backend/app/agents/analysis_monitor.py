"""分析监控 Agent — KPI 监控 + 综合分析 + 通用问答"""
from typing import Any, AsyncGenerator, Dict, List, Optional

from app.agents.base import BaseAgent
from app.core.prompts import ANALYSIS_MONITOR_SYSTEM_PROMPT


class AnalysisMonitorAgent(BaseAgent):
    name = "analysis_monitor"
    display_name = "分析监控"
    icon = "📊"
    color = "#6c5ce7"
    description = "分析监控助手：KPI趋势、偏差告警、综合分析报告、通用问答"

    system_prompt = ANALYSIS_MONITOR_SYSTEM_PROMPT

    async def call_tools(self, message: str) -> Optional[str]:
        return await self._call_tools_via_ontology(message)

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
        user_id: str = "",
    ) -> AsyncGenerator[tuple, None]:
        async for evt in self._standard_process(
            message, session_id, model_name, use_agent, web_search,
            enable_thinking, context, history_messages, matched_agents, user_id,
        ):
            yield evt


analysis_monitor_agent = AnalysisMonitorAgent()
