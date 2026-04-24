"""质检 Agent"""
from typing import Optional, Dict, Any, AsyncGenerator, List
from app.agents.base import BaseAgent
from app.agents.entity_extractor import extract_entities
from app.agents.tools.quality_tools import query_quality_report, query_quality_summary, analyze_defects, format_quality
from app.core.logger import log
from app.core.prompts import QUALITY_SYSTEM_PROMPT
from app.services.llm_service import llm_service


class QualityAgent(BaseAgent):
    name = "quality"
    display_name = "质检助手"
    icon = "🔍"
    color = "#e17055"
    description = "质量检测分析、缺陷诊断和良率提升"
    keywords = ["质检", "质量", "不合格", "次品", "良率", "检测", "抽检", "返工", "报废", "不良", "合格率", "SPC"]
    system_prompt = QUALITY_SYSTEM_PROMPT

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
        entities = await extract_entities(message, domain="quality")
        product = entities.get("product")

        if "分析" in message or "缺陷" in message or "不良" in message:
            return await analyze_defects(product)
        if "报告" in message:
            reports = await query_quality_report(product)
            return format_quality(reports)
        if "概况" in message or "合格率" in message:
            summary = await query_quality_summary()
            return f"今日检测 {summary['total_inspected']} 件，合格 {summary['total_passed']} 件，不良 {summary['total_failed']} 件，合格率 {summary['overall_rate']}%（目标 {summary['target_rate']}%）"
        return None


quality_agent = QualityAgent()
