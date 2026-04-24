"""Agent 抽象基类"""
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, AsyncGenerator, List
from app.core.logger import log


class BaseAgent(ABC):
    """所有 Agent 的抽象基类"""

    name: str = ""
    display_name: str = ""
    icon: str = "🤖"
    color: str = "#6c5ce7"
    description: str = ""
    system_prompt: str = ""

    def get_info(self) -> Dict[str, str]:
        """返回 Agent 元信息"""
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
    ) -> AsyncGenerator[tuple, None]:
        """
        处理用户消息，流式返回响应

        Yields:
            (type, content) 元组，type 为 'thinking' / 'content' / 'error'
        """
        pass

    async def call_tools(self, message: str) -> Optional[str]:
        """调用领域工具，返回格式化结果文本"""
        return None

    async def build_system_prompt(
        self,
        memory_context: Optional[str] = None
    ) -> str:
        """构建系统提示词（可被子类覆盖）"""
        prompt = self.system_prompt
        if memory_context:
            prompt += f"\n\n## 相关历史记忆\n\n{memory_context}"
        return prompt

    def __repr__(self):
        return f"<Agent: {self.display_name} ({self.name})>"
