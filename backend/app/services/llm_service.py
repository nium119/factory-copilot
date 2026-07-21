import asyncio
import json
from typing import AsyncGenerator, Dict, List, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

from app.core.config import settings
from app.core.logger import log
from app.core.model_config import get_api_key, get_model_config
from app.core.prompts import DEFAULT_SYSTEM_PROMPT, SIMPLE_SYSTEM_PROMPT
from app.core.resource_monitor import resource_monitor


def _estimate_tokens(text: str) -> int:
    """粗略估算 token 数：中文 1 token/字，英文/数字约 4 字符/token"""
    cjk = sum(1 for c in text if '一' <= c <= '鿿')
    ascii_chars = len(text) - cjk
    return cjk + max(1, ascii_chars // 4)


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

            api_key = get_api_key(model_config["provider"], target_model)
            if not api_key:
                raise ValueError(f"未配置 {model_config['provider']} 的API密钥")

            llm_kwargs = {
                "model": target_model,
                "temperature": settings.AGENT_TEMPERATURE,
                "max_tokens": model_config["max_tokens"],
                "openai_api_base": model_config["api_base"],
                "openai_api_key": api_key,
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
        enable_thinking: Optional[bool] = None,
        tools: Optional[List[Dict]] = None,
        user_id: str = "",
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
            enable_thinking: 是否启用深度思考（None=使用模型默认值）
            tools: OpenAI function calling 工具定义列表
            user_id: 当前用户 ID，用于数据权限过滤（空字符串=不过滤）

        Yields:
            (type, content) 元组
        """
        try:
            self._initialize_llm(model_name)

            target_model = model_name or settings.AGENT_MODEL
            model_config = get_model_config(target_model)
            provider = model_config["provider"]
            model_default_thinking = model_config.get("enable_thinking", False)

            log.info(f"处理流式聊天请求 - 会话: {session_id}, 模型: {target_model}, 深度思考: {enable_thinking}, 联网搜索: {web_search}, tools: {len(tools or [])}")

            if resource_monitor.enabled:
                resource_monitor.record_api_call()
                resource_monitor.record_tokens(_estimate_tokens(message))

            context_messages = history_messages or []

            effective_prompt = system_prompt or DEFAULT_SYSTEM_PROMPT

            # None = 使用模型默认值；True/False = 用户显式控制
            should_think = model_default_thinking if enable_thinking is None else enable_thinking
            log.info(f"[LLM] target_model={target_model}, enable_thinking={enable_thinking}, model_default={model_default_thinking}, should_think={should_think}, provider={provider}")

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
                    enable_thinking=True, enable_search=True, model_config=model_config, tools=tools,
                    user_id=user_id,
                ):
                    yield chunk
            # 深度思考模式
            elif should_think:
                async for chunk in self._chat_stream_normal(
                    message, effective_prompt, context_messages,
                    enable_thinking=True, enable_search=False, model_config=model_config, tools=tools,
                    user_id=user_id,
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
                    model_config=model_config, tools=tools,
                    user_id=user_id,
                ):
                    yield chunk

        except Exception as e:
            log.error(f"流式聊天处理失败: {str(e)}")
            raise

    def reset_for_summary(self):
        """重置初始化状态，供摘要生成时使用独立模型"""
        self._initialized = False
        self.current_model = None

    async def chat_sync(
        self,
        message: str,
        session_id: str = "default",
        system_prompt: Optional[str] = None,
        model_name: str = None,
    ) -> str:
        """同步聊天（用于反思等非流式场景）"""
        self._initialize_llm(model_name)
        prompt = system_prompt or DEFAULT_SYSTEM_PROMPT
        messages = [SystemMessage(content=prompt), HumanMessage(content=message)]

        async def _invoke():
            return await self.llm.ainvoke(messages)

        response = await asyncio.wait_for(_invoke(), timeout=120.0)
        return response.content if response.content else ""

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
            full_content = ""

            async for event in self.agent.astream(inputs, stream_mode="values"):
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
        model_config: Optional[Dict] = None,
        tools: Optional[List[Dict]] = None,
        user_id: str = "",
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
                    all_messages, model_config, enable_search=enable_search, tools=tools,
                    user_id=user_id,
                ):
                    yield chunk
            elif tools:
                async for chunk in self._stream_with_tools(all_messages, model_config, tools, user_id=user_id):
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

    async def _stream_with_tools(
        self,
        all_messages: List,
        model_config: Dict,
        tools: List[Dict],
        user_id: str = "",
    ) -> AsyncGenerator[tuple, None]:
        """Stream + tools mode — handle tool_calls with execution loop.

        1. Bind tools to LLM
        2. Call LLM with tool definitions
        3. If LLM returns tool_calls → execute → feed results back → call LLM again
        4. Stream final text response
        """
        from langchain_core.messages import AIMessage, ToolMessage
        from openai import AsyncOpenAI

        MAX_ROUNDS = 5
        TOOL_ROUNDS = 3  # 前 3 轮可调工具，之后必须产内容

        try:
            _api_key = get_api_key(model_config["provider"], self.current_model)
            _client = AsyncOpenAI(api_key=_api_key, base_url=model_config["api_base"])
            known_tool_names = {t['function']['name'] for t in tools} if tools else set()

            for _round in range(MAX_ROUNDS):
                # ── Build OpenAI-format messages ──
                _api_messages = []
                for _msg in all_messages:
                    if isinstance(_msg, SystemMessage):
                        _api_messages.append({"role": "system", "content": _msg.content})
                    elif isinstance(_msg, HumanMessage):
                        _api_messages.append({"role": "user", "content": _msg.content})
                    elif isinstance(_msg, AIMessage):
                        _entry = {"role": "assistant", "content": _msg.content or ""}
                        if _msg.tool_calls:
                            _tc_list = []
                            for _tc in _msg.tool_calls:
                                _tc_list.append({
                                    "id": _tc.get("id", ""),
                                    "type": "function",
                                    "function": {
                                        "name": _tc.get("name", ""),
                                        "arguments": json.dumps(_tc.get("args", {}), ensure_ascii=False),
                                    },
                                })
                            _entry["tool_calls"] = _tc_list
                        _api_messages.append(_entry)
                    elif isinstance(_msg, ToolMessage):
                        _api_messages.append({
                            "role": "tool",
                            "content": _msg.content,
                            "tool_call_id": _msg.tool_call_id,
                        })
                    elif hasattr(_msg, 'role'):
                        _api_messages.append({
                            "role": _msg.role,
                            "content": _msg.content,
                            "tool_call_id": getattr(_msg, 'tool_call_id', None),
                        })

                # ── Build API call params ──
                _params = {"model": self.current_model, "messages": _api_messages, "stream": True}
                if tools and _round < TOOL_ROUNDS:
                    _params["tools"] = tools
                if model_config["provider"] == "qwen":
                    _qe = _build_qwen_extra_body(disable_thinking=True)
                    if _qe:
                        _params["extra_body"] = _qe

                # ── Stream and collect ──
                _content = ""
                _tool_calls_acc: Dict[int, Dict] = {}

                _stream = await _client.chat.completions.create(**_params)
                async for _chunk in _stream:
                    if not _chunk.choices:
                        continue
                    _d = _chunk.choices[0].delta
                    if _d.content:
                        _content += _d.content
                    if hasattr(_d, 'tool_calls') and _d.tool_calls:
                        for _tc in _d.tool_calls:
                            if _tc is None:
                                continue
                            _idx = getattr(_tc, 'index', 0)
                            if _idx not in _tool_calls_acc:
                                _tool_calls_acc[_idx] = {'id': '', 'name': '', 'arguments': ''}
                            if getattr(_tc, 'id', None):
                                _tool_calls_acc[_idx]['id'] = _tc.id
                            if getattr(_tc, 'function', None):
                                if getattr(_tc.function, 'name', None):
                                    _tool_calls_acc[_idx]['name'] = _tc.function.name
                                if getattr(_tc.function, 'arguments', None):
                                    _tool_calls_acc[_idx]['arguments'] += _tc.function.arguments

                # ── If content produced, yield and done ──
                if _content and not _tool_calls_acc:
                    yield ('content', _content)
                    return

                # ── If tool calls, execute and loop ──
                if _tool_calls_acc:
                    from app.services.action_executor import action_executor

                    for _tc_data in _tool_calls_acc.values():
                        _tool_name = _tc_data['name']
                        _parsed_args = {}
                        try:
                            _parsed_args = json.loads(_tc_data['arguments']) if _tc_data['arguments'] else {}
                        except (json.JSONDecodeError, TypeError):
                            pass

                        if known_tool_names and _tool_name not in known_tool_names:
                            log.warning(f"LLM hallucinated tool: {_tool_name}")
                            _result = f"[工具不存在] {_tool_name}"
                            _row_count = 0
                            _source = 'error'
                        else:
                            log.info(f"[_stream_with_tools] round={_round} tool={_tool_name}")
                            yield ('tool_call', json.dumps({
                                'id': _tc_data['id'], 'name': _tool_name, 'arguments': _parsed_args,
                            }))
                            _structured = await action_executor.execute_structured_async(
                                _tool_name, _parsed_args, user_id=user_id or "system",
                            )
                            _result = _structured.get('result', '')
                            _row_count = _structured.get('rowCount', 0)
                            _source = _structured.get('source', '')
                            log.info(f"Tool result: {str(_result)[:200]}")
                            yield ('tool_result', json.dumps({
                                'id': _tc_data['id'], 'name': _tool_name,
                                'rowCount': _row_count, 'source': _source,
                            }))

                        all_messages.append(AIMessage(
                            content="",
                            tool_calls=[{'id': _tc_data['id'], 'name': _tool_name, 'args': _parsed_args}],
                        ))
                        all_messages.append(ToolMessage(content=_result, tool_call_id=_tc_data['id']))
                    continue  # loop back for next round

                # ── No content and no tool calls: empty response ──
                yield ('content', '')
                return

            # ── Exhausted rounds ──
            log.warning(f"[_stream_with_tools] exhausted {MAX_ROUNDS} rounds, forcing empty response")
            yield ('content', '')

        except Exception as e:
            log.error(f"流式+Tools模式处理失败: {str(e)}")
            yield ('content', f"[工具调用异常: {e}]")

    async def _stream_with_thinking(
        self,
        all_messages: List,
        model_config: Dict,
        enable_search: bool = False,
        tools: Optional[List[Dict]] = None,
        user_id: str = "",
    ) -> AsyncGenerator[tuple, None]:
        """使用AsyncOpenAI SDK流式获取思考过程，支持 tool calling + 执行循环"""
        try:
            from openai import AsyncOpenAI

            api_key = get_api_key(model_config["provider"], self.current_model)
            client = AsyncOpenAI(
                api_key=api_key,
                base_url=model_config["api_base"]
            )

            def _to_api_messages(msgs):
                result = []
                for msg in msgs:
                    if isinstance(msg, SystemMessage):
                        result.append({"role": "system", "content": msg.content})
                    elif isinstance(msg, HumanMessage):
                        result.append({"role": "user", "content": msg.content})
                    elif isinstance(msg, AIMessage):
                        if hasattr(msg, 'tool_calls') and msg.tool_calls:
                            result.append({"role": "assistant", "content": msg.content or "", "tool_calls": msg.tool_calls})
                        else:
                            result.append({"role": "assistant", "content": msg.content or ""})
                    elif hasattr(msg, 'role'):
                        result.append({"role": msg.role, "content": msg.content, "tool_call_id": getattr(msg, 'tool_call_id', None)})
                return result

            api_messages = _to_api_messages(all_messages)

            stream_kwargs = {
                "model": self.current_model,
                "messages": api_messages,
                "stream": True,
            }

            if tools:
                stream_kwargs["tools"] = tools

            if model_config["provider"] == "qwen":
                qwen_extra = _build_qwen_extra_body(
                    enable_thinking=True,
                    enable_search=enable_search
                )
                if qwen_extra:
                    stream_kwargs["extra_body"] = qwen_extra

            # ── First pass: LLM with tools ──
            stream = await client.chat.completions.create(**stream_kwargs)
            tool_calls_acc: Dict[int, Dict] = {}
            thinking_seen = False
            content_seen = False

            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if hasattr(delta, 'reasoning_content') and delta.reasoning_content:
                    thinking_seen = True
                    log.info(f"[LLM] _stream_with_thinking 收到 reasoning_content, len={len(delta.reasoning_content)}")
                    yield ('thinking', delta.reasoning_content)
                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        idx = tc.index
                        if idx not in tool_calls_acc:
                            tool_calls_acc[idx] = {'id': tc.id or '', 'name': '', 'arguments': ''}
                        if tc.function and tc.function.name:
                            tool_calls_acc[idx]['name'] = tc.function.name
                        if tc.id:
                            tool_calls_acc[idx]['id'] = tc.id
                        if tc.function and tc.function.arguments:
                            tool_calls_acc[idx]['arguments'] += tc.function.arguments
                if delta.content:
                    content_seen = True
                    yield ('content', delta.content)

            # ── If no tool calls, done ──
            if not tool_calls_acc:
                if not thinking_seen:
                    log.warning(f"[LLM] _stream_with_thinking: 没有收到任何 reasoning_content (模型={self.current_model})")
                return

            # ── Execute tools ──
            from app.services.action_executor import action_executor

            known_tool_names_think = {t['function']['name'] for t in tools} if tools else set()

            for tc_data in tool_calls_acc.values():
                tool_name_think = tc_data['name']
                if known_tool_names_think and tool_name_think not in known_tool_names_think:
                    log.warning(f"LLM hallucinated tool name (thinking): {tool_name_think}")
                    yield ('tool_call', json.dumps({
                        'id': tc_data.get('id', ''),
                        'name': tool_name_think,
                        'arguments': {},
                    }))
                    result = f"[工具不存在] {tool_name_think} 不在可用工具列表中。可用的工具: {', '.join(sorted(known_tool_names_think))}"
                    row_count = 0
                    source = 'error'
                else:
                    log.info(f"Executing tool (thinking): {tool_name_think}")
                    parsed_args = {}
                    try:
                        parsed_args = json.loads(tc_data['arguments']) if tc_data['arguments'] else {}
                    except (json.JSONDecodeError, TypeError):
                        parsed_args = {}
                    yield ('tool_call', json.dumps({
                        'id': tc_data['id'],
                        'name': tool_name_think,
                        'arguments': parsed_args,
                    }))
                    structured = await action_executor.execute_structured_async(
                        tool_name_think, parsed_args, user_id=user_id or "system",
                    )
                    result = structured.get('result', '')
                    row_count = structured.get('rowCount', 0)
                    source = structured.get('source', '')
                log.info(f"Tool result: {result[:200]}")
                yield ('tool_result', json.dumps({
                    'id': tc_data['id'],
                    'name': tc_data['name'],
                    'rowCount': row_count,
                    'source': source,
                }))

                # 将工具调用和结果追加到消息列表
                api_messages.append({
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": tc_data['id'],
                        "type": "function",
                        "function": {
                            "name": tc_data['name'],
                            "arguments": tc_data['arguments'],
                        },
                    }],
                })
                api_messages.append({
                    "role": "tool",
                    "tool_call_id": tc_data['id'],
                    "content": result,
                })

            # ── Second pass: stream final response with tool results ──
            stream_kwargs2 = {
                "model": self.current_model,
                "messages": api_messages,
                "stream": True,
            }
            if model_config["provider"] == "qwen":
                # Qwen 模型：disable_thinking=True 才能显式关闭思考模式
                if qwen_extra := _build_qwen_extra_body(disable_thinking=True, enable_search=enable_search):
                    stream_kwargs2["extra_body"] = qwen_extra

            stream2 = await client.chat.completions.create(**stream_kwargs2)
            async for chunk in stream2:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta.content:
                    yield ('content', delta.content)

        except Exception as e:
            log.error(f"思考过程流式输出失败: {str(e)}")
            async for chunk in self.llm.astream(all_messages):
                if chunk.content:
                    yield ('content', chunk.content)

# 单例实例
llm_service = LLMService()
