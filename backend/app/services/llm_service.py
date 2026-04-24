from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langgraph.prebuilt import create_react_agent
from app.core.config import settings
from app.core.logger import log
from app.core.model_config import get_model_config, get_api_key
from app.core.prompts import DEFAULT_SYSTEM_PROMPT, SIMPLE_SYSTEM_PROMPT
from typing import List, Dict, Any, Optional, AsyncGenerator
import os
import asyncio


def _build_qwen_extra_body(
    enable_thinking: bool = False,
    enable_search: bool = False,
    disable_thinking: bool = False,
) -> Optional[Dict]:
    """
    构建Qwen模型需要的extra_body参数
    """
    extra_body = {}
    if enable_thinking:
        extra_body["enable_thinking"] = True
    if disable_thinking:
        extra_body["enable_thinking"] = False
    if enable_search:
        extra_body["enable_search"] = True
        extra_body["search_options"] = {"forced_search": True}
    return extra_body if extra_body else None


class LLMService:
    """LLM服务 - 使用LangChain"""

    def __init__(self):
        self.llm = None
        self.agent = None
        self._initialized = False
        self.current_model = None

    def _initialize_llm(self, model_name: str = None):
        """初始化LLM（不带模型配置参数，由调用时动态传递）"""
        if self._initialized and model_name == self.current_model:
            return

        try:
            target_model = model_name or settings.AGENT_MODEL
            model_config = get_model_config(target_model)

            api_key = get_api_key(model_config["provider"])
            if not api_key:
                raise ValueError(f"未配置 {model_config['provider']} 的API密钥")

            os.environ["OPENAI_API_KEY"] = api_key

            llm_kwargs = {
                "model": target_model,
                "temperature": settings.AGENT_TEMPERATURE,
                "max_tokens": model_config["max_tokens"],
                "openai_api_base": model_config["api_base"],
            }

            self.llm = ChatOpenAI(**llm_kwargs)
            self.current_model = target_model
            self._initialized = True

            # 如果模型支持思考，创建Agent
            if model_config["enable_thinking"]:
                self._create_agent()

            log.info(f"LLM初始化成功: {target_model} (Provider: {model_config['provider']}, Thinking: {model_config['enable_thinking']})")

        except Exception as e:
            log.error(f"LLM初始化失败: {str(e)}")
            raise

    def _create_agent(self):
        """创建Agent用于深度思考"""
        try:
            self.agent = create_react_agent(self.llm, [])
            log.info("Agent创建成功")
        except Exception as e:
            log.warning(f"Agent创建失败,将使用普通模式: {str(e)}")
            self.agent = None

    async def chat_stream(
        self,
        message: str,
        session_id: str = "default",
        system_prompt: Optional[str] = None,
        model_name: str = None,
        use_agent: bool = False,
        web_search: bool = False,
        history_messages: Optional[List] = None,
        enable_thinking: bool = False
    ) -> AsyncGenerator[tuple, None]:
        """
        流式聊天对话

        Args:
            message: 用户消息
            session_id: 会话ID
            system_prompt: 系统提示词
            model_name: 模型名称
            use_agent: 是否启用协作模式（多 Agent 并发查询）
            web_search: 是否启用联网搜索
            history_messages: 外部传入的历史消息列表
            enable_thinking: 是否启用深度思考

        Yields:
            (type, content) 元组
        """
        try:
            self._initialize_llm(model_name)

            target_model = model_name or settings.AGENT_MODEL
            model_config = get_model_config(target_model)
            provider = model_config["provider"]
            user_enable_thinking = enable_thinking
            model_default_thinking = model_config.get("enable_thinking", False)

            log.info(f"处理流式聊天请求 - 会话: {session_id}, 模型: {target_model}, 深度思考: {user_enable_thinking}, 联网搜索: {web_search}")

            context_messages = history_messages or []

            effective_prompt = system_prompt or DEFAULT_SYSTEM_PROMPT

            should_think = user_enable_thinking or model_default_thinking

            # Qwen模型 + 联网搜索 → 使用模型内置联网搜索
            if provider == "qwen" and web_search and not should_think:
                async for chunk in self._chat_stream_qwen_search(
                    message, effective_prompt, context_messages, model_config
                ):
                    yield chunk
            # Qwen模型 + 联网搜索 + 深度思考 → 使用AsyncOpenAI
            elif provider == "qwen" and web_search and should_think:
                async for chunk in self._chat_stream_normal(
                    message, effective_prompt, context_messages,
                    enable_thinking=True, enable_search=True, model_config=model_config
                ):
                    yield chunk
            # 深度思考模式
            elif should_think:
                async for chunk in self._chat_stream_normal(
                    message, effective_prompt, context_messages,
                    enable_thinking=True, enable_search=False, model_config=model_config
                ):
                    yield chunk
            elif use_agent and self.agent:
                async for chunk in self._chat_with_agent(message, context_messages):
                    yield chunk
            else:
                # 普通流式模式
                disable_thinking = model_config.get("enable_thinking", False)
                async for chunk in self._chat_stream_normal(
                    message, effective_prompt, context_messages,
                    enable_thinking=False, enable_search=False,
                    disable_thinking=disable_thinking,
                    model_config=model_config
                ):
                    yield chunk

        except Exception as e:
            log.error(f"流式聊天处理失败: {str(e)}")
            raise

    def reset_for_summary(self):
        """重置初始化状态，供摘要生成时使用独立模型"""
        self._initialized = False
        self.current_model = None

    async def _chat_stream_qwen_search(
        self,
        message: str,
        system_prompt: str,
        context_messages: List,
        model_config: Dict,
    ) -> AsyncGenerator[tuple, None]:
        """Qwen模型内置联网搜索模式"""
        try:
            all_messages = []
            all_messages.append(SystemMessage(content=system_prompt))
            all_messages.extend(context_messages)
            all_messages.append(HumanMessage(content=message))

            extra_body = _build_qwen_extra_body(enable_search=True)

            log.info(f"Qwen内置联网搜索模式 - extra_body: {extra_body}")

            async for chunk in self.llm.astream(all_messages, extra_body=extra_body):
                if chunk.content:
                    yield ('content', chunk.content)

        except Exception as e:
            log.error(f"Qwen内置联网搜索模式失败: {str(e)}")
            async for chunk in self._chat_stream_normal(
                message, system_prompt, context_messages,
                enable_thinking=False, enable_search=False, model_config=model_config
            ):
                yield chunk

    async def _chat_with_agent(
        self,
        message: str,
        context_messages: List,
    ) -> AsyncGenerator[tuple, None]:
        """使用Agent模式进行深度思考"""
        try:
            log.info(f"使用Agent模式处理消息: {message[:50]}...")

            inputs = {"messages": [HumanMessage(content=message)]}

            def run_agent():
                full_content = ""
                for event in self.agent.stream(inputs, stream_mode="values"):
                    if "messages" in event:
                        for msg in event["messages"]:
                            if hasattr(msg, 'content') and msg.content:
                                if msg.content not in full_content:
                                    new_content = msg.content[len(full_content):]
                                    if new_content:
                                        full_content = msg.content
                                        if hasattr(msg, 'tool_calls') and msg.tool_calls:
                                            yield ('thinking', new_content)
                                        else:
                                            yield ('content', new_content)
                return full_content

            loop = asyncio.get_event_loop()
            for chunk_type, chunk_content in await loop.run_in_executor(None, lambda: list(run_agent())):
                yield (chunk_type, chunk_content)

        except Exception as e:
            log.error(f"Agent模式处理失败: {str(e)}")

    async def _chat_stream_normal(
        self,
        message: str,
        system_prompt: Optional[str],
        context_messages: List,
        enable_thinking: bool = False,
        enable_search: bool = False,
        disable_thinking: bool = False,
        model_config: Optional[Dict] = None
    ) -> AsyncGenerator[tuple, None]:
        """流式聊天模式"""
        try:
            if model_config is None:
                model_config = get_model_config(self.current_model)

            all_messages = []

            if system_prompt:
                all_messages.append(SystemMessage(content=system_prompt))
            else:
                all_messages.append(SystemMessage(content=SIMPLE_SYSTEM_PROMPT))

            all_messages.extend(context_messages)

            user_message = HumanMessage(content=message)
            all_messages.append(user_message)

            if enable_thinking:
                async for chunk in self._stream_with_thinking(
                    all_messages, model_config, enable_search=enable_search
                ):
                    yield chunk
            else:
                extra_body = None
                if model_config["provider"] == "qwen":
                    extra_body = _build_qwen_extra_body(
                        enable_thinking=enable_thinking,
                        enable_search=enable_search,
                        disable_thinking=disable_thinking,
                    )
                    log.info(f"普通模式 - extra_body: {extra_body}")

                async for chunk in self.llm.astream(all_messages, extra_body=extra_body if extra_body else None):
                    if chunk.content:
                        yield ('content', chunk.content)

        except Exception as e:
            log.error(f"流式模式处理失败: {str(e)}")
            raise

    async def _stream_with_thinking(
        self,
        all_messages: List,
        model_config: Dict,
        enable_search: bool = False
    ) -> AsyncGenerator[tuple, None]:
        """使用AsyncOpenAI SDK流式获取思考过程"""
        try:
            from openai import AsyncOpenAI

            api_key = get_api_key(model_config["provider"])
            client = AsyncOpenAI(
                api_key=api_key,
                base_url=model_config["api_base"]
            )

            api_messages = []
            for msg in all_messages:
                if isinstance(msg, SystemMessage):
                    api_messages.append({"role": "system", "content": msg.content})
                elif isinstance(msg, HumanMessage):
                    api_messages.append({"role": "user", "content": msg.content})
                elif isinstance(msg, AIMessage):
                    api_messages.append({"role": "assistant", "content": msg.content})

            stream_kwargs = {
                "model": self.current_model,
                "messages": api_messages,
                "stream": True,
            }

            if model_config["provider"] == "qwen":
                qwen_extra = _build_qwen_extra_body(
                    enable_thinking=True,
                    enable_search=enable_search
                )
                if qwen_extra:
                    stream_kwargs["extra_body"] = qwen_extra

            stream = await client.chat.completions.create(**stream_kwargs)
            async for chunk in stream:
                delta = chunk.choices[0].delta
                if hasattr(delta, 'reasoning_content') and delta.reasoning_content:
                    yield ('thinking', delta.reasoning_content)
                if delta.content:
                    yield ('content', delta.content)

        except Exception as e:
            log.error(f"思考过程流式输出失败: {str(e)}")
            async for chunk in self.llm.astream(all_messages):
                if chunk.content:
                    yield ('content', chunk.content)

# 单例实例
llm_service = LLMService()
