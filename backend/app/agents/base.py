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

    # ── Confirmation mechanism ──

    _pending_confirmations: dict = {}  # session_id → asyncio.Event

    @classmethod
    def resolve_confirmation(cls, session_id: str, approved: bool, params: dict = None):
        """Called by API endpoint to resolve a pending confirmation."""
        entry = cls._pending_confirmations.get(session_id)
        if entry:
            entry["approved"] = approved
            entry["params"] = params or {}
            entry["event"].set()
            return True
        return False

    async def _wait_for_confirmation(self, session_id: str, timeout: float = 60) -> tuple:
        """Wait for frontend to confirm or cancel. Returns (approved, params)."""
        import asyncio
        event = asyncio.Event()
        entry = {"event": event, "approved": False, "params": {}}
        self._pending_confirmations[session_id] = entry
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
            return entry["approved"], entry.get("params", {})
        except asyncio.TimeoutError:
            log.warning(f"[Confirm] session {session_id} 确认超时 ({timeout}s)")
            return False, {}
        finally:
            self._pending_confirmations.pop(session_id, None)

    # ── L2 LLM classification ──

    async def _llm_classify_action(
        self, message: str, candidates: list, model_name: Optional[str]
    ) -> Optional[str]:
        """L2: Use LLM to classify message into one of the known action names.

        candidates is a list of dicts: [{"name": "WorkOrder_query", "label": "查询工单", "description": "..."}, ...]
        Returns the fn_name or None.
        """
        from app.services.llm_service import llm_service

        if not candidates:
            return None

        options = "\n".join(
            f"- {c['name']}: {c['label']}（{c.get('concept_label', '')}） — {c.get('description', '')}"
            for c in candidates
        )
        classify_prompt = (
            "你是一个制造业领域的意图分类器。用户会用自然语言描述需求，你需要找到语义最匹配的操作。\n"
            "注意：用户可能使用口语化表达，请根据语义理解其意图，不要只做关键词匹配。\n"
            "例如：「生产质量怎么样」→ QualityCheck_query（查询质检记录）\n"
            "例如：「设备状态如何」→ Equipment_query（查询设备）\n"
            "只返回操作名称（如 WorkOrder_query），不要返回任何其他内容。\n"
            "如果没有任何操作匹配，返回 NONE。\n\n"
            f"可选操作：\n{options}\n\n"
            f"用户消息：{message}\n\n"
            "最匹配的操作名称："
        )

        result = ""
        try:
            async for t, c in llm_service.chat_stream(
                message=classify_prompt, session_id="l2_classify",
                system_prompt="你是一个制造业意图分类器。根据语义（而非字面关键词）将用户消息映射到最匹配的操作，只返回操作名称或NONE。",
                model_name=model_name,
                enable_thinking=False,
                tools=None,
            ):
                if t == 'content':
                    result += c
            result = result.strip().strip('"').strip("'")
            if result and result != "NONE":
                # Validate it's a known action
                known = {c['name'] for c in candidates}
                if result in known:
                    log.info(f"[L2 Classify] LLM classified as: {result}")
                    return result
                # Try fuzzy match — find candidate that contains or is contained by result
                for name in known:
                    if name in result or result in name:
                        log.info(f"[L2 Classify] fuzzy match: {result} → {name}")
                        return name
                log.warning(f"[L2 Classify] LLM returned unknown action: {result}")
        except Exception as e:
            log.warning(f"[L2 Classify] LLM classification failed: {e}")
        return None

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
        """标准处理流程：本体路由 → 参数提取 → 确认 → 执行 → LLM 格式化"""
        import json as _json
        from app.services.llm_service import llm_service

        if enable_thinking is None and self.should_deep_think(message):
            enable_thinking = True
            log.info(f"[{self.name}] 自动启用深度思考")

        # Get ontology tools for this agent
        onto_tools = None
        try:
            from app.services.ontology_service import ontology_service
            onto_tools = ontology_service.get_tools_for_agent(self.name)
            if onto_tools:
                log.info(f"[{self.name}] 加载了 {len(onto_tools)} 个本体工具")
        except Exception:
            pass

        # ── Ontology-driven deterministic routing ──
        if onto_tools:
            try:
                from app.services.intent_router import intent_router, RoutingResult
                from app.services.action_executor import action_executor

                if not intent_router.ready:
                    intent_router.rebuild(ontology_service, action_executor)

                if intent_router.ready:
                    yield ('route_start', _json.dumps({
                        "agent": self.name, "message": message[:100],
                    }))

                    routing_result = intent_router.route(message, self.name)

                    # Multi-domain queries: skip L1 keyword match when
                    # multiple agent domains were detected at the agent-routing
                    # layer. Keyword scoring is unreliable for cross-concept
                    # queries — let L2 LLM classify semantically instead.
                    if (routing_result.method == "keyword"
                            and matched_agents
                            and len(matched_agents) >= 2):
                        log.info(
                            f"[{self.name}] multi-domain detected ({matched_agents}),"
                            f" bypassing L1 keyword result ({routing_result.tool_name})"
                        )
                        routing_result = RoutingResult(method="l3", available_actions=[
                            a for a in routing_result.available_actions
                        ] or list(intent_router._index.values()))

                    # L1 failed → try L2 LLM classification
                    if not routing_result.tool_name and routing_result.method == "l3":
                        candidates = routing_result.available_actions
                        if candidates:
                            yield ('route_l2', _json.dumps({
                                "candidateCount": len(candidates),
                            }))
                            l2_name = await self._llm_classify_action(
                                message, candidates, model_name,
                            )
                            if l2_name:
                                routing_result = intent_router.route_explicit(l2_name, message)

                    # L3: no match → list available actions
                    if not routing_result.tool_name:
                        yield ('route_l3', _json.dumps({
                            "available": routing_result.available_actions,
                        }))
                        actions_text = "\n".join(
                            f"- **{a['label']}**：{a.get('description', '')}"
                            for a in routing_result.available_actions
                        )
                        reply = (
                            f"抱歉，我没有完全理解您的需求。以下是我能帮您做的事情：\n\n"
                            f"{actions_text}\n\n"
                            f"请明确您的需求，例如「查询生产中的工单」或「查看所有设备状态」。"
                        )
                        yield ('content', reply)
                        yield ('execution_done', _json.dumps({
                            "totalSteps": 2, "method": "l3",
                        }))
                        return

                    # ── Matched! ──
                    yield ('route_match', _json.dumps({
                        "method": routing_result.method,
                        "tool": routing_result.tool_name,
                        "confidence": routing_result.confidence,
                        "concept_label": routing_result.concept_label,
                        "action_label": routing_result.action_label,
                    }))

                    # ── Confirmation check ──
                    if routing_result.requires_confirmation:
                        # L1: extract params from message for pre-filling
                        prefill = routing_result.params or {}
                        if not prefill:
                            prefill = intent_router.extract_params(message, routing_result.tool_name)
                        # L3: ontology graph traversal — enrich params + context
                        enriched = await intent_router.enrich_params(routing_result.tool_name, prefill)
                        param_schema = intent_router.get_param_schema(routing_result.tool_name)
                        yield ('confirm_required', _json.dumps({
                            "tool": routing_result.tool_name,
                            "action_label": routing_result.action_label,
                            "concept_label": routing_result.concept_label,
                            "params": enriched.get('params', {}),
                            "param_schema": param_schema,
                            "risk": "write",
                            "context": enriched.get('context', {}),
                        }))
                        approved, params = await self._wait_for_confirmation(session_id, timeout=60)
                        yield ('confirm_result', _json.dumps({"approved": approved, "params": params}))
                        if not approved:
                            yield ('content', "操作已取消。如需执行，请重新发送指令。")
                            yield ('execution_done', _json.dumps({
                                "totalSteps": 4, "cancelled": True,
                            }))
                            return
                    else:
                        # Query operations: extract params from message
                        params = routing_result.params
                        if not params:
                            params = intent_router.extract_params(message, routing_result.tool_name)
                        # Async entity resolution via DataBackend
                        params = await intent_router.resolve_entities(
                            message, routing_result.tool_name, params,
                        )
                        yield ('param_extract', _json.dumps({
                            "params": params, "tool": routing_result.tool_name,
                        }))

                    # ── Execute tool ──
                    yield ('tool_start', _json.dumps({
                        "tool": routing_result.tool_name, "params": params,
                    }))
                    tool_result = await action_executor.execute_structured_async(
                        routing_result.tool_name, params,
                    )
                    yield ('tool_result', _json.dumps({
                        "tool": routing_result.tool_name,
                        "rowCount": tool_result.get("rowCount", 0),
                        "source": tool_result.get("source", ""),
                    }))

                    # ── LLM format only ──
                    yield ('format_start', _json.dumps({}))

                    from app.core.prompts import FORMAT_ONLY_SYSTEM_PROMPT
                    tool_result_text = tool_result.get("result", "")
                    format_message = (
                        f"### 查询结果\n{tool_result_text}\n\n"
                        f"### 用户消息\n{message}\n\n"
                        f"请基于以上查询结果回复用户消息。"
                    )

                    system_prompt = await self.build_system_prompt(include_tools_prompt=False)
                    system_prompt = f"{FORMAT_ONLY_SYSTEM_PROMPT}\n\n{system_prompt}"

                    async for t, c in llm_service.chat_stream(
                        message=format_message, session_id=session_id,
                        system_prompt=system_prompt,
                        model_name=model_name,
                        use_agent=False, web_search=False,
                        history_messages=history_messages,
                        enable_thinking=enable_thinking,
                        tools=None,  # NO tools — format only
                    ):
                        yield t, c

                    yield ('execution_done', _json.dumps({
                        "totalSteps": 6, "method": routing_result.method,
                        "tool": routing_result.tool_name,
                    }))
                    return

            except Exception as e:
                log.error(f"[{self.name}] 本体路由异常: {e}", exc_info=True)
                yield ('content', f"处理请求时发生错误，请稍后重试。")
                yield ('execution_done', _json.dumps({
                    "totalSteps": 0, "error": str(e),
                }))
                return

        # ── 未配置本体工具：直接返回错误，不回退到 LLM 自由调用 ──
        yield ('content', f"该 Agent（{self.display_name}）未配置本体工具，无法处理请求。请联系管理员配置 ontology。")
        yield ('execution_done', _json.dumps({
            "totalSteps": 0, "error": "no_ontology_tools",
        }))

    async def call_tools(self, message: str) -> Optional[str]:
        """调用领域工具，返回格式化结果文本"""
        return None

    async def _call_tools_via_ontology(self, message: str) -> Optional[str]:
        """通过本体链路执行工具：IntentRouter 路由 + 参数提取 + action_executor 执行。

        完全零硬编码 — 路由关键词、参数提取器、代码模式全部从本体 Action/Concept 定义自动生成。
        子类可覆盖 call_tools()，在其中优先调用本方法，再用自身逻辑兜底。
        """
        try:
            from app.services.intent_router import intent_router
            from app.services.action_executor import action_executor
            from app.services.ontology_service import ontology_service

            if not intent_router.ready:
                intent_router.rebuild(ontology_service, action_executor)

            if not intent_router.ready:
                return None

            result = intent_router.route(message, self.name)
            if not result.tool_name:
                return None

            params = result.params or intent_router.extract_params(message, result.tool_name)
            tool_result = await action_executor.execute_structured_async(result.tool_name, params)
            return tool_result.get("result", "") if tool_result else None
        except Exception as e:
            log.warning(f"[{self.name}] 本体路由执行失败: {e}")
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
        include_tools_prompt: bool = False,
    ) -> str:
        """构建系统提示词（含本体上下文、记忆上下文和推理框架）"""
        prompt = self.system_prompt
        if reasoning_context:
            prompt += f"\n\n{reasoning_context}"

        # Inject ontology domain knowledge if available
        try:
            from app.services.ontology_service import ontology_service
            onto_prompt = ontology_service.get_prompt_for_agent(self.name)
            if onto_prompt:
                prompt += f"\n\n## 领域本体模型\n\n{onto_prompt}"
            if include_tools_prompt:
                tools = ontology_service.get_tools_for_agent(self.name)
                if tools:
                    tool_names = [t.get("function", {}).get("name", "") for t in tools]
                    prompt += f"\n\n## 可用工具（必须优先使用）\n当用户查询数据时，你必须调用工具函数获取真实数据，严禁编造数据。可用工具列表：{', '.join(tool_names)}"
            # Inject business rules from ontology
            rules = ontology_service.get_rules_for_agent(self.name)
            if rules:
                rules_text = "\n".join(
                    f"- [{r['ruleType']}] **{r['concept']}.{r['label']}**: {r['description']} (表达式: {r['expression']})"
                    for r in rules
                )
                prompt += f"\n\n## 业务规则（必须遵守）\n以下是从本体模型中提取的业务规则，你在回答和操作时必须遵守：\n{rules_text}"
        except Exception:
            pass

        if memory_context:
            prompt += f"\n\n## 相关历史记忆\n\n{memory_context}"
        return prompt

    def __repr__(self):
        return f"<Agent: {self.display_name} ({self.name})>"
