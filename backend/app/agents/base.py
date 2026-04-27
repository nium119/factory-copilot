"""Agent 抽象基类"""
import asyncio
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, AsyncGenerator, List
from app.core.logger import log
from app.agents.settings import RETRY_CONFIG


class BaseAgent(ABC):
    """所有 Agent 的抽象基类"""

    name: str = ""
    display_name: str = ""
    icon: str = "🤖"
    color: str = "#6c5ce7"
    description: str = ""
    system_prompt: str = ""

    def get_info(self) -> Dict[str, str]:
        """返回 Agent 元数据"""
        return {
            "name": self.name,
            "display_name": self.display_name,
            "icon": self.icon,
            "color": self.color,
            "description": self.description,
        }

    @abstractmethod
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
        """处理用户消息，流式返回响应"""
        pass

    async def call_tools(self, message: str) -> Optional[str]:
        """调用领域工具，返回格式化结果文本"""
        return None

    async def call_tools_with_retry(self, message: str, max_retries: int = None) -> Tuple[Optional[str], Optional[str]]:
        """带重试和分类错误处理的工具调用包装器

        Returns:
            (result, error_hint): 工具结果和可选的错误提示
        """
        from app.agents.error_handler import classify_error, backoff_delay, get_recovery_suggestion, ErrorClass

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

    async def build_system_prompt(
        self,
        memory_context: Optional[str] = None
    ) -> str:
        """构建系统提示词"""
        prompt = self.system_prompt
        if memory_context:
            prompt += f"\n\n## 相关历史记忆\n\n{memory_context}"
        return prompt

    def __repr__(self):
        return f"<Agent: {self.display_name} ({self.name})>"
