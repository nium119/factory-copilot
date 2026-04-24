from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent
from app.core.config import settings
from app.core.logger import log
from app.core.model_config import get_model_config, get_api_key
from app.core.prompts import (
    DEFAULT_SYSTEM_PROMPT,
    SIMPLE_SYSTEM_PROMPT,
    THINK_TOOL_DESCRIPTION,
    format_web_search_prompt,
    format_enterprise_query_prompt,
    get_system_prompt,
)
from typing import List, Dict, Any, Optional, AsyncGenerator
import os
import asyncio
import re


def _build_qwen_extra_body(
    enable_thinking: bool = False,
    enable_search: bool = False,
    disable_thinking: bool = False,
) -> Optional[Dict]:
    """
    构建Qwen模型需要的extra_body参数

    Args:
        enable_thinking: 是否启用深度思考
        enable_search: 是否启用联网搜索
        disable_thinking: 显式禁用思考（覆盖模型默认配置）

    Returns:
        extra_body字典，无参数时返回None
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
        self.message_history = {}  # 内存备份，兼容旧接口
        self._initialized = False
        self.current_model = None

    def _initialize_llm(self, model_name: str = None):
        """初始化LLM（不包含任何内置工具参数，由调用时动态传递）"""
        if self._initialized and model_name == self.current_model:
            return
        
        try:
            target_model = model_name or settings.AGENT_MODEL
            model_config = get_model_config(target_model)
            
            api_key = get_api_key(model_config["provider"])
            if not api_key:
                raise ValueError(f"未配置 {model_config['provider']} 的API密钥")
            
            os.environ["OPENAI_API_KEY"] = api_key
            
            # 初始化ChatOpenAI，不在初始化时设置enable_search/enable_thinking
            # 这些参数由每次调用时通过extra_body动态传入
            llm_kwargs = {
                "model": target_model,
                "temperature": settings.AGENT_TEMPERATURE,
                "max_tokens": model_config["max_tokens"],
                "openai_api_base": model_config["api_base"],
            }
            
            self.llm = ChatOpenAI(**llm_kwargs)
            self.current_model = target_model
            self._initialized = True
            
            # 如果模型支持思考,创建Agent
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

    def _get_messages(self, session_id: str) -> List:
        """获取会话消息历史（内存）"""
        if session_id not in self.message_history:
            self.message_history[session_id] = []
        return self.message_history[session_id]

    def _get_provider(self) -> str:
        """获取当前模型的提供商"""
        model_config = get_model_config(self.current_model)
        return model_config["provider"]

    async def chat(
        self,
        message: str,
        session_id: str = "default",
        system_prompt: Optional[str] = None,
        history_messages: Optional[List] = None
    ) -> str:
        """聊天对话"""
        try:
            self._initialize_llm()
            log.info(f"处理聊天请求 - 会话: {session_id}, 消息: {message[:50]}...")

            if history_messages is not None:
                context_messages = history_messages
            else:
                context_messages = self._get_messages(session_id)

            all_messages = []
            if system_prompt:
                all_messages.append(SystemMessage(content=system_prompt))
            else:
                all_messages.append(SystemMessage(content=SIMPLE_SYSTEM_PROMPT))
            all_messages.extend(context_messages)
            user_message = HumanMessage(content=message)
            all_messages.append(user_message)

            response = self.llm.invoke(all_messages)

            mem_messages = self._get_messages(session_id)
            mem_messages.append(user_message)
            mem_messages.append(AIMessage(content=response.content))

            log.info(f"聊天响应成功 - 会话: {session_id}")
            return response.content

        except Exception as e:
            log.error(f"聊天处理失败: {str(e)}")
            raise

    async def chat_stream(
        self,
        message: str,
        session_id: str = "default",
        system_prompt: Optional[str] = None,
        model_name: str = None,
        use_agent: bool = False,
        web_search: bool = False,
        history_messages: Optional[List] = None
    ) -> AsyncGenerator[tuple, None]:
        """
        流式聊天对话

        Args:
            message: 用户消息
            session_id: 会话ID
            system_prompt: 系统提示词
            model_name: 模型名称
            use_agent: 是否使用Agent模式(深度思考)
            web_search: 是否启用联网搜索
            history_messages: 外部传入的历史消息列表

        Yields:
            (type, content) 元组
        """
        try:
            self._initialize_llm(model_name)
            
            target_model = model_name or settings.AGENT_MODEL
            model_config = get_model_config(target_model)
            provider = model_config["provider"]
            enable_thinking = model_config["enable_thinking"]
            
            log.info(f"处理流式聊天请求 - 会话: {session_id}, 模型: {target_model}, 深度思考: {use_agent}, 联网搜索: {web_search}")

            if history_messages is not None:
                context_messages = history_messages
                log.info(f"使用外部传入的历史消息({len(context_messages)}条)作为上下文")
            else:
                context_messages = self._get_messages(session_id)
                log.info(f"使用内存中的历史消息({len(context_messages)}条)作为上下文")
            
            effective_prompt = system_prompt or DEFAULT_SYSTEM_PROMPT
            
            # Qwen模型 + 联网搜索 → 使用模型内置联网搜索（Chat Completions API的enable_search）
            if provider == "qwen" and web_search and not (enable_thinking and use_agent):
                # 非深度思考模式下，通过LangChain ChatOpenAI的extra_body传递enable_search
                async for chunk in self._chat_stream_qwen_search(
                    message, session_id, effective_prompt, context_messages, model_config
                ):
                    yield chunk
            # Qwen模型 + 联网搜索 + 深度思考 → 使用AsyncOpenAI同时传递enable_search和enable_thinking
            elif provider == "qwen" and web_search and enable_thinking and use_agent:
                async for chunk in self._chat_stream_normal(
                    message, session_id, effective_prompt, context_messages,
                    enable_thinking=True, enable_search=True, model_config=model_config
                ):
                    yield chunk
            # 深度思考模式
            elif enable_thinking and use_agent:
                async for chunk in self._chat_stream_normal(
                    message, session_id, effective_prompt, context_messages,
                    enable_thinking=True, enable_search=False, model_config=model_config
                ):
                    yield chunk
            elif use_agent and self.agent:
                async for chunk in self._chat_with_agent(message, session_id, context_messages):
                    yield chunk
            else:
                # 普通流式模式
                disable_thinking = model_config.get("enable_thinking", False)
                async for chunk in self._chat_stream_normal(
                    message, session_id, effective_prompt, context_messages,
                    enable_thinking=False, enable_search=False,
                    disable_thinking=disable_thinking,
                    model_config=model_config
                ):
                    yield chunk

        except Exception as e:
            log.error(f"流式聊天处理失败: {str(e)}")
            raise

    def clear_memory(self, session_id: str) -> bool:
        """清除会话记忆"""
        if session_id in self.message_history:
            del self.message_history[session_id]
            log.info(f"已清除会话记忆: {session_id}")
            return True
        return False
    
    async def _chat_stream_qwen_search(
        self,
        message: str,
        session_id: str,
        system_prompt: str,
        context_messages: List,
        model_config: Dict
    ) -> AsyncGenerator[tuple, None]:
        """
        Qwen模型内置联网搜索模式（Chat Completions API的enable_search）

        通过LangChain ChatOpenAI的astream方法，在调用时传递extra_body参数
        """
        try:
            all_messages = []
            all_messages.append(SystemMessage(content=system_prompt))
            all_messages.extend(context_messages)
            all_messages.append(HumanMessage(content=message))
            
            # 构建Qwen联网搜索的extra_body
            extra_body = _build_qwen_extra_body(enable_search=True)
            
            log.info(f"Qwen内置联网搜索模式 - extra_body: {extra_body}")
            
            # 使用LangChain ChatOpenAI的astream，传递extra_body
            full_response = ""
            async for chunk in self.llm.astream(all_messages, extra_body=extra_body):
                if chunk.content:
                    full_response += chunk.content
                    yield ('content', chunk.content)
            
            # 保存到内存历史
            mem_messages = self._get_messages(session_id)
            mem_messages.append(HumanMessage(content=message))
            mem_messages.append(AIMessage(content=full_response))
            
            log.info(f"Qwen内置联网搜索模式处理完成 - 会话: {session_id}")
            
        except Exception as e:
            log.error(f"Qwen内置联网搜索模式处理失败: {str(e)}")
            # 降级到普通模式
            async for chunk in self._chat_stream_normal(
                message, session_id, system_prompt, context_messages,
                enable_thinking=False, enable_search=False, model_config=model_config
            ):
                yield chunk
    
    async def _chat_with_agent(
        self,
        message: str,
        session_id: str,
        context_messages: List
    ) -> AsyncGenerator[tuple, None]:
        """使用Agent模式进行深度思考"""
        try:
            log.info(f"使用Agent模式处理消息: {message[:50]}...")
            
            inputs = {"messages": [HumanMessage(content=message)]}
            config = {"configurable": {"thread_id": session_id}}
            
            def run_agent():
                full_content = ""
                for event in self.agent.stream(inputs, config=config, stream_mode="values"):
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
            full_response = ""
            for chunk_type, chunk_content in await loop.run_in_executor(None, lambda: list(run_agent())):
                full_response += chunk_content
                yield (chunk_type, chunk_content)
            
            mem_messages = self._get_messages(session_id)
            mem_messages.append(HumanMessage(content=message))
            mem_messages.append(AIMessage(content=full_response))
            
            log.info("Agent模式处理完成")
            
        except Exception as e:
            log.error(f"Agent模式处理失败: {str(e)}")
    
    async def _chat_stream_normal(
        self,
        message: str,
        session_id: str,
        system_prompt: Optional[str],
        context_messages: List,
        enable_thinking: bool = False,
        enable_search: bool = False,
        disable_thinking: bool = False,
        model_config: Optional[Dict] = None
    ) -> AsyncGenerator[tuple, None]:
        """
        流式聊天模式

        Args:
            message: 用户消息
            session_id: 会话ID
            system_prompt: 系统提示词
            context_messages: 上下文消息列表
            enable_thinking: 是否启用深度思考
            enable_search: 是否启用联网搜索（Qwen内置）
            model_config: 模型配置
        """
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
                # 普通流式调用，动态传递extra_body
                extra_body = None
                if model_config["provider"] == "qwen":
                    extra_body = _build_qwen_extra_body(
                        enable_thinking=enable_thinking,
                        enable_search=enable_search,
                        disable_thinking=disable_thinking,
                    )
                    log.info(f"普通模式 - extra_body: {extra_body}")

                full_response = ""
                async for chunk in self.llm.astream(all_messages, extra_body=extra_body if extra_body else None):
                    if chunk.content:
                        full_response += chunk.content
                        yield ('content', chunk.content)
                
                mem_messages = self._get_messages(session_id)
                mem_messages.append(user_message)
                mem_messages.append(AIMessage(content=full_response))
            
            log.info(f"流式模式处理完成 - 会话: {session_id}, 思考: {enable_thinking}, 搜索: {enable_search}")
            
        except Exception as e:
            log.error(f"流式模式处理失败: {str(e)}")
            raise
    
    async def _stream_with_thinking(
        self,
        all_messages: List,
        model_config: Dict,
        enable_search: bool = False
    ) -> AsyncGenerator[tuple, None]:
        """
        使用AsyncOpenAI SDK流式获取思考过程

        Args:
            all_messages: 消息列表
            model_config: 模型配置
            enable_search: 是否同时启用联网搜索
        """
        try:
            from openai import AsyncOpenAI
            
            api_key = get_api_key(model_config["provider"])
            client = AsyncOpenAI(
                api_key=api_key,
                base_url=model_config["api_base"]
            )
            
            # 转换消息格式
            api_messages = []
            for msg in all_messages:
                if isinstance(msg, SystemMessage):
                    api_messages.append({"role": "system", "content": msg.content})
                elif isinstance(msg, HumanMessage):
                    api_messages.append({"role": "user", "content": msg.content})
                elif isinstance(msg, AIMessage):
                    api_messages.append({"role": "assistant", "content": msg.content})
            
            # 构建流式请求参数
            stream_kwargs = {
                "model": self.current_model,
                "messages": api_messages,
                "stream": True,
            }
            
            # Qwen模型：构建extra_body（包含enable_thinking，可选enable_search）
            if model_config["provider"] == "qwen":
                qwen_extra = _build_qwen_extra_body(
                    enable_thinking=True,
                    enable_search=enable_search
                )
                if qwen_extra:
                    stream_kwargs["extra_body"] = qwen_extra
                    log.info(f"深度思考+联网搜索 - extra_body: {qwen_extra}")
            
            # 使用AsyncOpenAI直接异步流式调用
            stream = await client.chat.completions.create(**stream_kwargs)
            async for chunk in stream:
                delta = chunk.choices[0].delta
                if hasattr(delta, 'reasoning_content') and delta.reasoning_content:
                    yield ('thinking', delta.reasoning_content)
                if delta.content:
                    yield ('content', delta.content)
            
            log.info(f"思考过程流式输出完成")
            
        except Exception as e:
            log.error(f"思考过程流式输出失败: {str(e)}")
            # 降级到普通模式（不带enable_search，因为搜索失败不应该再重试搜索）
            full_response = ""
            async for chunk in self.llm.astream(all_messages):
                if chunk.content:
                    full_response += chunk.content
                    yield ('content', chunk.content)

    def get_memory_content(self, session_id: str) -> List[Dict[str, Any]]:
        """获取会话记忆内容"""
        if session_id not in self.message_history:
            return []

        messages = self.message_history[session_id]
        result = []
        for msg in messages:
            if isinstance(msg, HumanMessage):
                result.append({"role": "user", "content": msg.content})
            elif isinstance(msg, AIMessage):
                result.append({"role": "assistant", "content": msg.content})

        return result

# 单例实例
llm_service = LLMService()