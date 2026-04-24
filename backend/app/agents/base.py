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

    async def call_tools_with_retry(self, message: str, max_retries: int = None) -> Optional[str]:
        """带重试保护的工具调用包装器"""
        if max_retries is None:
            max_retries = RETRY_CONFIG["max_retries"]
        last_error = None
        for attempt in range(max_retries + 1):
            try:
                result = await self.call_tools(message)
                if result:
                    return result
                if attempt < max_retries:
                    log.warning(f"{self.name} 返回空结果，重试 {attempt + 1}/{max_retries}")
                    await asyncio.sleep(RETRY_CONFIG["empty_result_delay"])
            except Exception as e:
                last_error = e
                if attempt < max_retries:
                    log.warning(f"{self.name} 工具调用失败 (尝试 {attempt + 1}/{max_retries}): {e}")
                    await asyncio.sleep(RETRY_CONFIG["exception_delay"])
                else:
                    log.warning(f"{self.name} 工具调用失败，已达最大重试: {e}")
        return f"[工具调用失败: {last_error}]" if last_error else None

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
