"""质检 Agent"""
from typing import Optional, Dict, Any, AsyncGenerator, List
from app.agents.base import BaseAgent
from app.agents.agent_config import AGENT_DEFINITIONS
from app.agents.settings import REFLECTION_ACTIONABLE_KEYWORDS
from app.agents.entity_extractor import extract_entities
from app.agents.tools.quality_tools import query_quality_report, query_quality_summary, analyze_defects, format_quality
from app.core.logger import log
from app.core.prompts import QUALITY_SYSTEM_PROMPT
from app.services.llm_service import llm_service


class QualityAgent(BaseAgent):
    _meta = AGENT_DEFINITIONS["quality"]
    name = "quality"
    display_name = _meta["display_name"]
    icon = _meta["icon"]
    color = _meta["color"]
    description = _meta["description"]
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
        matched_agents: Optional[List[str]] = None,
    ) -> AsyncGenerator[tuple, None]:
        # 缺陷分析/根因追溯自动启用深度思考
        if enable_thinking is None and self.should_deep_think(message):
            enable_thinking = True
            log.info(f"[Quality] 自动启用深度思考: {message[:50]}...")

        tool_result = await self.call_tools(message)
        enhanced = f"{message}\n\n参考数据:\n{tool_result}" if tool_result else message

        # 缺陷分析场景：发出结构化推理步骤
        reasoning_framework = ""
        if "分析" in message or "缺陷" in message or "不良" in message:
            from app.core.prompts import REASONING_TEMPLATES
            reasoning_framework = REASONING_TEMPLATES.get("quality_root_cause", "")
            async for evt in self.emit_reasoning_steps(message):
                yield evt

        system_prompt = context.get("system_prompt", self.system_prompt) if context else self.system_prompt
        if reasoning_framework:
            system_prompt = await self.build_system_prompt(reasoning_context=reasoning_framework)

        async for t, c in llm_service.chat_stream(
            message=enhanced, session_id=session_id,
            system_prompt=system_prompt,
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

    async def reflect(self, message: str, response: str) -> Optional[str]:
        """质检响应自检规则"""
        actionable_kw = REFLECTION_ACTIONABLE_KEYWORDS["quality"]
        issues = []

        if any(k in message for k in ["产品", "良率", "缺陷", "报告"]) and response:
            entities = await extract_entities(message, domain="quality")
            product = entities.get("product")
            if product and product not in response:
                issues.append(f"用户查询涉及产品 '{product}'，但响应中未提及")

        if "合格率" in message and "目标" not in response and "target" not in response.lower():
            issues.append("合格率查询应包含目标值对比")

        if ("缺陷" in message or "不良" in message) and response:
            if not any(k in response for k in actionable_kw):
                issues.append("缺陷分析应给出可操作的改进建议")

        if issues:
            log.info(f"[QualityAgent] 反思发现问题，触发 LLM 修正: {issues}")
            fix_prompt = (
                f"你的上一个响应存在以下不足：{'；'.join(issues)}。\n\n"
                f"用户原始问题：{message}\n\n"
                f"你之前的回答：{response}\n\n"
                f"请重新生成一份更完整的回答，确保覆盖上述问题。"
            )
            try:
                result = await llm_service.chat_sync(
                    message=fix_prompt, session_id="reflection", system_prompt=self.system_prompt,
                )
                return result
            except Exception as e:
                log.warning(f"[QualityAgent] LLM 修正失败: {e}")
        return None


quality_agent = QualityAgent()
