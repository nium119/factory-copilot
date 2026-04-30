"""Agent 抽象基类"""
import asyncio
from abc import ABC
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

from app.agents.settings import RETRY_CONFIG
from app.core.logger import log


class BaseAgent(ABC):
    """所有 Agent 的抽象基类 — 子类只需定义 name + system_prompt + call_tools()"""

    name: str = ""
    display_name: str = ""
    icon: str = "🤖"
    color: str = "#6c5ce7"
    description: str = ""
    system_prompt: str = ""

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if cls.name:
            try:
                from app.agents.agent_config import AGENT_DEFINITIONS
                if cls.name in AGENT_DEFINITIONS:
                    meta = AGENT_DEFINITIONS[cls.name]
                    for attr in ('display_name', 'icon', 'color', 'description'):
                        if not getattr(cls, attr, None):
                            setattr(cls, attr, meta.get(attr, ''))
            except ImportError:
                pass

    def __init__(self):
        self._session_id: str = "default"

    async def _safe_call(self, tool_name: str, tool_fn, *args, **kwargs) -> Any:
        """安全工具调用包装：自动携带当前会话上下文进行审批/审计"""
        from app.agents.guardrails import safe_tool_call
        return await safe_tool_call(
            tool_name, tool_fn, *args,
            session_id=getattr(self, '_session_id', 'default'),
            **kwargs,
        )

    def get_info(self) -> Dict[str, str]:
        """返回 Agent 元数据"""
        return {
            "name": self.name,
            "display_name": self.display_name,
            "icon": self.icon,
            "color": self.color,
            "description": self.description,
        }

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
        """处理用户消息，流式返回响应 — 子类可覆盖"""
        if not hasattr(self, '_standard_process'):
            raise NotImplementedError
        async for evt in self._standard_process(
            message, session_id, model_name, use_agent, web_search,
            enable_thinking, context, history_messages, matched_agents,
        ):
            yield evt

    async def _standard_process(
        self,
        message: str,
        session_id: str,
        model_name: Optional[str],
        use_agent: bool,
        web_search: bool,
        enable_thinking: Optional[bool],
        context: Optional[Dict[str, Any]],
        history_messages: Optional[List],
        matched_agents: Optional[List[str]],
    ) -> AsyncGenerator[tuple, None]:
        """标准处理流程：自动深度思考 → 工具调用 → 推理框架 → LLM 流式"""
        from app.services.llm_service import llm_service

        if enable_thinking is None and self.should_deep_think(message):
            enable_thinking = True
            log.info(f"[{self.name}] 自动启用深度思考")

        tool_result = await self.call_tools(message)
        enhanced = message
        if tool_result:
            if isinstance(tool_result, tuple):
                enhanced_text, eval_data = tool_result
                if eval_data:
                    yield ('eval_result', eval_data)
            else:
                enhanced_text = tool_result
            enhanced = f"{message}\n\n参考数据:\n{enhanced_text}" if enhanced_text else message

        reasoning_framework = self._get_reasoning_framework(message)
        if reasoning_framework:
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
        """调用领域工具，返回格式化结果文本"""
        return None

    async def call_tools_with_retry(self, message: str, max_retries: int = None) -> Tuple[Optional[str], Optional[str]]:
        """带重试和分类错误处理的工具调用包装器

        Returns:
            (result, error_hint): 工具结果和可选的错误提示
        """
        from app.agents.error_handler import ErrorClass, backoff_delay, classify_error, get_recovery_suggestion

        if max_retries is None:
            max_retries = RETRY_CONFIG["max_retries"]

        last_error_class = None
        last_error = None

        for attempt in range(max_retries + 1):
            try:
                result = await self.call_tools(message)
                if result:
                    if attempt > 0:
                        log.info(f"{self.name} 重试成功 (尝试 {attempt + 1}/{max_retries + 1})")
                    return result, None
                if attempt < max_retries:
                    if RETRY_CONFIG.get("use_exponential_backoff"):
                        delay = backoff_delay(
                            attempt,
                            RETRY_CONFIG["exponential_backoff_base"],
                            RETRY_CONFIG["exponential_backoff_max"],
                        )
                    else:
                        delay = RETRY_CONFIG["empty_result_delay"]
                    log.warning(f"{self.name} 返回空结果，{delay:.1f}s 后重试 {attempt + 1}/{max_retries}")
                    await asyncio.sleep(delay)
            except Exception as e:
                last_error = e
                last_error_class = classify_error(e)
                if attempt < max_retries:
                    if RETRY_CONFIG.get("use_exponential_backoff"):
                        delay = backoff_delay(
                            attempt,
                            RETRY_CONFIG["exponential_backoff_base"],
                            RETRY_CONFIG["exponential_backoff_max"],
                        )
                    else:
                        delay = RETRY_CONFIG["exception_delay"]
                    log.warning(
                        f"{self.name} 工具调用失败 [{last_error_class.value}] "
                        f"(尝试 {attempt + 1}/{max_retries}): {e}，{delay:.1f}s 后重试"
                    )
                    await asyncio.sleep(delay)
                else:
                    log.warning(f"{self.name} 工具调用失败，已达最大重试 [{last_error_class.value}]: {e}")

        error_hint = get_recovery_suggestion(last_error_class or ErrorClass.UNKNOWN) if last_error_class else None
        error_text = f"[工具调用失败: {last_error}]" if last_error else None
        return error_text, error_hint

    async def reflect(self, message: str, response: str) -> Optional[str]:
        """自我反思：检查响应是否完整、准确"""
        return None
    def should_deep_think(self, message: str) -> bool:
        """检查消息是否需要启用深度思考（基于 REASONING_CONFIG 关键词）"""
        from app.agents.settings import REASONING_CONFIG
        auto_keywords = REASONING_CONFIG.get("auto_think_keywords", {}).get(self.name, [])
        return any(k in message for k in auto_keywords)

    def get_reasoning_steps(self) -> list:
        """获取当前 Agent 的结构化推理步骤定义"""
        from app.agents.settings import REASONING_CONFIG
        agent_key = f"{self.name}_diagnosis_steps"
        return REASONING_CONFIG.get(
            agent_key,
            REASONING_CONFIG.get(f"{self.name}_root_cause_steps", [])
        )

    async def emit_reasoning_steps(self, message: str):
        """生成结构化推理步骤 SSE 事件（供 process() 方法 yield 使用）"""
        import json as _json

        from app.agents.settings import REASONING_CONFIG
        if not REASONING_CONFIG.get("enabled", False):
            return
        steps = self.get_reasoning_steps()
        if not steps:
            return
        yield ('reasoning_start', _json.dumps({"agent": self.name, "steps": steps}))
        for step in steps:
            yield ('reasoning_step', _json.dumps({"key": step["key"], "label": step["label"], "icon": step.get("icon", "")}))

    def _get_reasoning_framework(self, message: str) -> str:
        """获取推理框架模板 — 子类可覆盖以在特定场景下注入结构化推理 (如故障诊断)"""
        return ""

    async def build_system_prompt(
        self,
        memory_context: Optional[str] = None,
        reasoning_context: Optional[str] = None,
    ) -> str:
        """构建系统提示词（含记忆上下文和推理框架）"""
        prompt = self.system_prompt
        if reasoning_context:
            prompt += f"\n\n{reasoning_context}"
        if memory_context:
            prompt += f"\n\n## 相关历史记忆\n\n{memory_context}"
        return prompt

    def __repr__(self):
        return f"<Agent: {self.display_name} ({self.name})>"
