"""Agent 抽象基类"""
import asyncio
from abc import ABC
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

from app.agents.settings import RETRY_CONFIG
from app.core.config import settings
from app.core.logger import log


def _inject_where_clause(cypher: str, condition: str) -> str:
    """向 Cypher 语句在 MATCH 变量作用域内注入 AND 过滤条件。

    在 RETURN/WITH/ORDER BY/LIMIT/SKIP 之前插入 AND condition，
    如果已有 WHERE 则存在已有的条件之后再追加。
    """
    import re as _re

    # Match the segment between MATCH and the next major clause
    m = _re.search(
        r'(MATCH\b.*?)(\bRETURN\b|\bWITH\b|\bORDER\s+BY\b|\bLIMIT\b|\bSKIP\b)',
        cypher, _re.IGNORECASE | _re.DOTALL,
    )
    if not m:
        return cypher

    match_segment = m.group(1)
    after_match = m.group(2)
    rest = cypher[m.end(2):]

    if _re.search(r'\bWHERE\b', match_segment, _re.IGNORECASE):
        new_segment = match_segment + f" AND {condition}"
    else:
        new_segment = match_segment + f" WHERE {condition}"

    return new_segment + " " + after_match + rest


def _inject_scope_clause(cypher: str, var_name: str, scope_concept: str,
                         scope_property: str, scope_value_alias: str) -> str:
    """向 Cypher MATCH 段注入图遍历 scope 过滤。

    在 MATCH 段末尾追加 MATCH (var)-[*1..3]->(scope:ScopeConcept)
    并附加 WHERE scope.property = $alias，用于多工厂数据隔离。
    """
    import re as _re

    m = _re.search(
        r'(MATCH\b.*?)(\bRETURN\b|\bWITH\b|\bORDER\s+BY\b|\bLIMIT\b|\bSKIP\b)',
        cypher, _re.IGNORECASE | _re.DOTALL,
    )
    if not m:
        return cypher

    match_section = m.group(1)
    after_keyword = m.group(2)
    rest = cypher[m.end(2):]

    scope_clause = (
        f" MATCH ({var_name})-[:*1..3]->(scope:{scope_concept}) "
        f"WHERE scope.{scope_property} = ${scope_value_alias}"
    )

    return match_section + scope_clause + " " + after_keyword + rest


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
        # Auto-format domain in system prompt (replaces {domain} placeholder)
        if cls.system_prompt and '{domain}' in cls.system_prompt:
            try:
                from app.core.prompts import P
                cls.system_prompt = P(cls.system_prompt)
            except Exception:
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
            "project_description": getattr(self, "project_description", ""),
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
        user_id: str = "",
    ) -> AsyncGenerator[tuple, None]:
        """处理用户消息，流式返回响应 — 子类可覆盖"""
        if not hasattr(self, '_standard_process'):
            raise NotImplementedError
        async for evt in self._standard_process(
            message, session_id, model_name, use_agent, web_search,
            enable_thinking, context, history_messages, matched_agents, user_id,
        ):
            yield evt

    # ── Confirmation mechanism ──

    _pending_confirmations: dict = {}  # session_id → asyncio.Event

    @classmethod
    def resolve_confirmation(cls, session_id: str, approved: bool, params: dict = None):
        """Called by API endpoint to resolve a pending confirmation."""
        log.debug(f"[Confirm] resolve_confirmation called: session_id={session_id}, approved={approved}")
        entry = cls._pending_confirmations.get(session_id)
        if entry:
            entry["approved"] = approved
            entry["params"] = params or {}
            entry["event"].set()
            log.debug(f"[Confirm] resolve_confirmation SUCCESS: session_id={session_id}")
            return True
        log.warning(f"[Confirm] resolve_confirmation FAILED: session_id={session_id} not found in pending")
        return False

    def _prepare_confirmation(self, session_id: str) -> asyncio.Event:
        """Register a pending confirmation BEFORE yielding confirm_required.

        Returns the event that resolve_confirmation will set.
        This avoids a race where the frontend sends confirm before the
        generator is resumed and _wait_for_confirmation gets called.
        """
        event = asyncio.Event()
        entry = {"event": event, "approved": False, "params": {}}
        self._pending_confirmations[session_id] = entry
        log.debug(f"[Confirm] prepare: session_id={session_id}")
        return event

    async def _wait_for_confirmation(self, session_id: str, timeout: float = 60, event: asyncio.Event = None) -> tuple:
        """Wait for frontend to confirm or cancel. Returns (approved, params)."""
        if event is None:
            # Fallback: create our own entry (used by callers that don't pre-register)
            event = asyncio.Event()
            entry = {"event": event, "approved": False, "params": {}}
            self._pending_confirmations[session_id] = entry
            log.debug(f"[Confirm] _wait_for_confirmation registered: session_id={session_id}")
        else:
            log.debug(f"[Confirm] _wait_for_confirmation waiting: session_id={session_id}")
        try:
            if timeout is not None:
                await asyncio.wait_for(event.wait(), timeout=timeout)
            else:
                await event.wait()
            entry = self._pending_confirmations.get(session_id, {})
            log.debug(f"[Confirm] _wait_for_confirmation resolved: session_id={session_id}, approved={entry.get('approved', False)}")
            return entry.get("approved", False), entry.get("params", {})
        except asyncio.TimeoutError:
            log.warning(f"[Confirm] session {session_id} 确认超时 ({timeout}s)")
            return False, {}
        finally:
            self._pending_confirmations.pop(session_id, None)
            log.debug(f"[Confirm] _wait_for_confirmation cleanup: session_id={session_id}")

    # ── L2 LLM classification ──

    async def _llm_classify_action(
        self, message: str, candidates: list, model_name: Optional[str]
    ) -> Optional[str]:
        """L2: Use LLM to classify message into one of the known action names.

        candidates is a list of dicts: [{"name": "WorkOrder_query", "label": "查询工单", "description": "...", "concept_label": "工单"}, ...]
        Returns the fn_name or None.
        """
        from app.services.llm_service import llm_service

        if not candidates:
            return None

        # Group candidates by concept_label for scalable prompt layout
        groups: dict[str, list] = {}
        for c in candidates:
            key = c.get("concept_label", "其他")
            groups.setdefault(key, []).append(c)

        options_parts = []
        for concept_label, items in groups.items():
            options_parts.append(f"【{concept_label}】")
            for c in items:
                options_parts.append(f"  - {c['name']}: {c['label']} — {c.get('description', '')}")
        options = "\n".join(options_parts)

        # Read domain description from ontology (or use neutral default)
        domain_desc = "通用领域"
        try:
            from app.services.ontology_service import ontology_service
            meta = ontology_service.meta
            domain_desc = meta.get("description") or meta.get("projectName") or "通用领域"
        except Exception:
            pass

        classify_prompt = (
            f"你是一个{domain_desc}领域的意图分类器。用户会用自然语言描述需求，你需要找到语义最匹配的操作。\n"
            "注意：用户可能使用口语化表达，请根据语义理解其意图，不要只做关键词匹配。\n"
            "只返回操作名称（如 WorkOrder_query），不要返回任何其他内容。\n\n"
            "重要规则：\n"
            "1. 只有当用户意图与某个操作的语义高度吻合时，才返回该操作名\n"
            "2. 如果只是泛泛相关（如「整体产能」vs「查询工单」），返回 NONE\n"
            "3. 错误匹配比不匹配更糟糕——宁可返回 NONE，不要硬选\n"
            "4. 概括性、战略性、跨领域的提问（如「怎么样」「整体情况」「产能」「效率」），应返回 NONE\n"
            "5. 匹配用户当前要执行的操作，不要推理后续步骤。用户说的是当下要做什么，不是接下来会发生什么。\n"
            "   例如：「质检不合格」→ QualityCheck_record（记录质检结果），而非 WorkOrder_markAsRework（标记返工）\n"
            "   例如：「设备故障」→ AndonEvent_call（安灯呼叫），而非 Equipment_updateStatus（更新设备状态）\n\n"
            f"可选操作（按概念域分组）：\n{options}\n\n"
            f"用户消息：{message}\n\n"
            "最匹配的操作名称（NONE 或具体操作名）："
        )

        known = {c['name'] for c in candidates}

        # 触发词优先匹配 — 用户配置的触发词直接命中，跳过 LLM
        msg_lower = message.lower().strip()
        wants_create = any(w in message for w in ['创建', '新建', '添加', '新增', '登记', '录入'])
        wants_query = any(w in message for w in ['查询', '查看', '查找', '检索', '列出', '显示'])
        matched = []
        for c in candidates:
            concept_name = c.get('concept_name', '')
            action_name = c.get('name', '')
            is_create = '_create' in action_name or '_add' in action_name
            is_query = '_query' in action_name
            # 动作类型过滤
            if wants_create and not is_create:
                continue
            if wants_query and not is_query:
                continue
            # 加载该 action 的触发词
            try:
                from app.services.intent_router import _load_skill_triggers
                triggers = _load_skill_triggers(action_name) or []
                # 也加载 query 触发词（兜底：概念级触发词配置在 _query 上）
                if is_create:
                    triggers += _load_skill_triggers(f"{concept_name}_query") or []
                for t in triggers:
                    if t and (t in msg_lower or msg_lower in t):
                        matched.append((action_name, t, is_create))
                        break
            except Exception:
                pass

        if matched:
            # 有动作倾向时优先 create；否则优先 query
            if wants_create:
                create_match = next((m for m in matched if m[2]), None)
                if create_match:
                    log.info(f"[L2 Classify] trigger match: '{message}' -> {create_match[0]}")
                    return create_match[0]
            best = matched[0]
            log.info(f"[L2 Classify] trigger match: '{message}' -> {best[0]} (trigger='{best[1]}')")
            return best[0]

        try:
            classify_model = "qwen-turbo"  # lightweight, fast (<2s)
            result = await asyncio.wait_for(
                llm_service.chat_sync(
                    message=classify_prompt,
                    system_prompt=f"你是一个{domain_desc}意图分类器。只返回最匹配的操作名称，无匹配则返回NONE。概括/跨域提问必须返回NONE——宁可漏过不可错配。",
                    model_name=classify_model,
                ),
                timeout=8.0,
            )
            result = (result or "").strip().strip('"').strip("'")
            if result and result != "NONE":
                if result in known:
                    log.info(f"[L2 Classify] {result} ({len(candidates)} candidates, model={classify_model})")
                    return result
                for name in known:
                    if name in result or result in name:
                        log.info(f"[L2 Classify] fuzzy: {result} → {name}")
                        return name
                # Token overlap match (e.g. WorkOrder_report → WorkReport_report via "report")
                r_tokens = set(result.lower().split('_'))
                for name in known:
                    n_tokens = set(name.lower().split('_'))
                    common = r_tokens & n_tokens
                    if len(common) >= 2 or (len(common) == 1 and len(r_tokens - common) <= 1):
                        log.info(f"[L2 Classify] token fuzzy: {result} → {name} (common={common})")
                        return name
                log.warning(f"[L2 Classify] unknown action: {result}")
        except asyncio.TimeoutError:
            log.warning(f"[L2 Classify] timeout (8s) for {len(candidates)} candidates, falling back to keyword match")
        except Exception as e:
            log.warning(f"[L2 Classify] failed: {e}")

        # ── LLM fallback → fast keyword proximity match ──
        msg_lower = message.lower()
        best = None
        best_score = 0
        for c in candidates:
            label_lower = c.get('label', '').lower()
            name_lower = c['name'].lower()
            desc_lower = c.get('description', '').lower()
            concept_label = c.get('concept_label', '').lower()
            score = 0
            # Exact label match (e.g. "创建工单" ↔ WorkOrder_create "创建工单")
            # Also check if message is a substring of label (e.g. "工单" ↔ "查询工单")
            if label_lower and (label_lower in msg_lower or msg_lower in label_lower):
                score = 100
            elif concept_label and (concept_label in msg_lower or msg_lower in concept_label):
                score = 90
            elif name_lower in msg_lower:
                score = 80
            # Partial word match
            for word in msg_lower:
                if word in label_lower:
                    score += 1
                if word in name_lower:
                    score += 0.5
                if word in concept_label:
                    score += 1
            # Check if query/create action matches intent
            if ('创建' in message or '新建' in message or 'create' in msg_lower) and 'create' in name_lower:
                score += 50
            if ('查询' in message or '查看' in message or 'query' in msg_lower) and 'query' in name_lower:
                score += 50
            if ('报工' in message or '上报' in message or 'report' in msg_lower) and 'report' in name_lower:
                score += 50
            if ('质检' in message or '质量' in message or 'quality' in msg_lower) and ('quality' in name_lower or 'check' in name_lower):
                score += 30
            if score > best_score:
                best_score = score
                best = c['name']

        if best and best_score >= 50:
            log.info(f"[L2 Classify] keyword fallback: {best} (score={best_score})")
            return best
        log.warning(f"[L2 Classify] no match for '{message}', best={best} score={best_score}")
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
        user_id: str = "",
        _depth: int = 0,
    ) -> AsyncGenerator[tuple, None]:
        """标准处理流程：本体路由 → 参数提取 → 确认 → 执行 → LLM 格式化"""
        if _depth > 3:
            yield ('error', '处理链深度超限，请简化查询')
            return
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

        # ── 链检测：消息触发预定义的分析链（优先于 L2 路由）──
        chain_detected = False
        try:
            from app.core.chain_engine import OntologyChainEngine, reload_chains
            reload_chains()  # 确保读取最新链配置
            chain_engine = OntologyChainEngine()
            chain_id = chain_engine.detect(message)
            if chain_id:
                # 确保 agent resolver 使用当前流程的 agent
                chain_engine.set_agent_resolver(lambda name: self.__class__())
                log.info(f"[{self.name}] 触发链: {chain_id}")
                async for evt in chain_engine.execute(
                    message, chain_id=chain_id,
                    model_name=model_name, enable_thinking=enable_thinking,
                    session_id=session_id, history_messages=history_messages,
                ):
                    yield evt
                chain_detected = True
        except Exception as e:
            log.warning(f"[{self.name}] 链引擎执行失败: {e}")

        if chain_detected:
            return

        # ── Ontology-driven deterministic routing ──
        if onto_tools:
            try:
                from app.services.intent_router import intent_router, RoutingResult
                from app.services.action_executor import action_executor

                if not intent_router.ready:
                    intent_router.rebuild(ontology_service, action_executor)

                if intent_router.ready:
                    yield ('route_start', _json.dumps({
                        "agent": self.name, "display_name": self.display_name,
                        "message": message[:100],
                    }))

                    # L2 LLM semantic classification — bypass fragile keyword matching
                    candidates = intent_router.get_candidates(self.name)
                    candidate_list = [
                        {
                            "name": fn,
                            "label": e.action_label,
                            "description": e.description,
                            "concept_label": e.concept_label,
                            "concept_name": e.concept_name,
                        }
                        for fn, e in candidates.items()
                    ]
                    routing_result = RoutingResult()

                    if candidate_list:
                        concept_names = list(dict.fromkeys(
                            c["concept_name"] for c in candidate_list if c.get("concept_name")
                        ))
                        yield ('route_l2', _json.dumps({
                            "candidateCount": len(candidate_list),
                            "concepts": concept_names,
                        }))
                        l2_name = await self._llm_classify_action(
                            message, candidate_list, model_name,
                        )
                        if l2_name:
                            routing_result = intent_router.route_explicit(l2_name, message)

                    # L3: no L2 match → decide fallback vs list available actions
                    if not l2_name:
                        if settings.AGENT_FALLBACK_ENABLED and onto_tools:
                            log.info(f"[{self.name}] 本体路由无匹配，进入 LLM Agent 兜底")
                            async for evt in self._llm_agent_fallback(
                                message, session_id, model_name, enable_thinking,
                                history_messages, onto_tools, user_id,
                                concept_names=concept_names if candidate_list else None,
                            ):
                                yield evt
                            return
                        yield ('route_l3', _json.dumps({
                            "available": candidate_list,
                        }))
                        actions_text = "\n".join(
                            f"- **{a['label']}**：{a.get('description', '')}"
                            for a in candidate_list
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
                        # ── 确认路由：inline vs 委托审批 ──
                        from app.services.auth_service import auth_service as _auth_svc
                        user_roles = await _auth_svc.get_effective_roles(user_id) if user_id else set()
                        required_roles = set(routing_result.authorized_roles or [])
                        needs_delegation = required_roles and not (user_roles & required_roles)

                        # L1: extract params from message for pre-filling
                        # Run rule-based extraction first (exact regex/substring),
                        # then fall back to LLM params for anything not captured.
                        prefill = intent_router.extract_params(message, routing_result.tool_name)
                        # L2: resolve entity references from message (handles entity_lookup)
                        prefill = await intent_router.resolve_entities(
                            message, routing_result.tool_name, prefill,
                        )
                        # L3: fall back to LLM params for anything still empty
                        for k, v in (routing_result.params or {}).items():
                            if k not in prefill or not prefill.get(k):
                                prefill[k] = v
                        # L4: ontology graph traversal — enrich params + context
                        enriched = await intent_router.enrich_params(routing_result.tool_name, prefill)
                        param_schema = await intent_router.get_param_schema(routing_result.tool_name)

                        # 始终先走内联确认，用户确认后再分流
                        confirm_event = self._prepare_confirmation(session_id)
                        yield ('confirm_required', _json.dumps({
                            "tool": routing_result.tool_name,
                            "action_label": routing_result.action_label,
                            "concept_label": routing_result.concept_label,
                            "params": enriched.get('params', {}),
                            "param_schema": param_schema,
                            "risk": "write",
                            "context": enriched.get('context', {}),
                        }))
                        approved, confirmed_params = await self._wait_for_confirmation(session_id, timeout=None, event=confirm_event)
                        yield ('confirm_result', _json.dumps({"approved": approved, "params": confirmed_params}))
                        if not approved:
                            yield ('content', "操作已取消。如需执行，请重新发送指令。")
                            yield ('execution_done', _json.dumps({
                                "totalSteps": 4, "cancelled": True,
                            }))
                            return

                        # 确认后检查角色：用户无权限则委托审批
                        if needs_delegation:
                            yield ('confirm_delegated', _json.dumps({
                                "tool": routing_result.tool_name,
                                "action_label": routing_result.action_label,
                                "concept_label": routing_result.concept_label,
                                "params": confirmed_params or enriched.get('params', {}),
                                "param_schema": param_schema,
                                "risk": "write",
                                "assigned_to": list(required_roles),
                                "context": enriched.get('context', {}),
                            }))
                            assigned_role = list(required_roles)[0]
                            yield ('content', f"已确认操作并提交 **{assigned_role}** 审批。审批进度可在「待审批」菜单查看。")
                            yield ('execution_done', _json.dumps({
                                "totalSteps": 4, "cancelled": True, "delegated": True,
                            }))
                            return

                        params = confirmed_params
                    else:
                        # L1: extract params from message (rule-based, more accurate than LLM)
                        params = intent_router.extract_params(message, routing_result.tool_name)
                        # L2: resolve entity references from message (handles entity_lookup)
                        params = await intent_router.resolve_entities(
                            message, routing_result.tool_name, params,
                        )
                        # L3: fall back to LLM params for anything still empty
                        for k, v in (routing_result.params or {}).items():
                            if k not in params or not params.get(k):
                                params[k] = v
                        # 参数修正: 从原始消息提取编码, 优先填入主键 (意图路由的枚举匹配可能误识别)
                        import re as _re2
                        _m = _re2.search(r'[A-Z]{2,}[\d-]+', message)
                        if _m:
                            _cn = getattr(routing_result, 'concept_name', None) or routing_result.tool_name.replace("_query", "")
                            if _cn:
                                _concept = ontology_service.get_concept(_cn)
                                if _concept:
                                    for _prop in _concept.get("properties", []):
                                        if _prop.get("isPrimary"):
                                            # 清除旧的误识别参数
                                            for _old in list(params.keys()):
                                                if _old != _prop["name"]:
                                                    del params[_old]
                                            params[_prop["name"]] = _m.group()
                                            break

                        # Data filter injection — apply BEFORE param_extract so
                        # the frontend execution chain reflects the enforced filter.
                        applied_filters: list[str] = []
                        if user_id:
                            applied_filters = await action_executor.apply_data_filters(
                                routing_result.tool_name, user_id, params,
                            )
                        yield ('param_extract', _json.dumps({
                            "params": params, "tool": routing_result.tool_name,
                            "filters": applied_filters,
                        }))

                    # ── Execute tool ──
                    sig = action_executor._sigs.get(routing_result.tool_name, {})
                    yield ('tool_start', _json.dumps({
                        "tool": routing_result.tool_name,
                        "label": sig.get("actionLabel", "") or sig.get("conceptLabel", ""),
                        "params": params,
                    }))
                    async for reasoning_evt in self.emit_reasoning_steps(message):
                        yield reasoning_evt
                    tool_result = await action_executor.execute_structured_async(
                        routing_result.tool_name, params, user_id=user_id,
                    )
                    yield ('tool_result', _json.dumps({
                        "tool": routing_result.tool_name,
                        "label": sig.get("actionLabel", "") or sig.get("conceptLabel", ""),
                        "rowCount": tool_result.get("rowCount", 0),
                        "source": tool_result.get("source", ""),
                        "sourceLabel": tool_result.get("sourceLabel", ""),
                    }))

                    # Trigger alerts — structured event for frontend notification
                    for alert in tool_result.get("alerts", []) or []:
                        yield ('alert', _json.dumps(alert))

                    # Rule violation: stop here, don't format with LLM
                    if tool_result.get("source") == "rule_engine":
                        yield ('rule_violation', tool_result.get("result", ""))
                        yield ('content', tool_result.get("result", ""))
                        yield ('execution_done', _json.dumps({
                            "totalSteps": 4, "cancelled": True,
                        }))
                        return

                    # ── 约束规则审批门禁 ──
                    approvals = tool_result.get("approvals", [])
                    if tool_result.get("needs_approval") and approvals:
                        yield ('content', f"根据业务规则，此操作需要审批：\n" + "\n".join(
                            f"  • **{a.get('rule_label', '')}**: {a.get('description', '')}"
                            for a in approvals
                        ))
                        yield ('execution_done', _json.dumps({
                            "totalSteps": 4, "cancelled": True,
                        }))
                        return

                    # ── Inference confirmation gate ──
                    inferences = tool_result.get("inferences", [])
                    if tool_result.get("needs_inference_confirmation") and inferences:
                        confirm_payload = {
                            "type": "inference_chain",
                            "tool": routing_result.tool_name,
                            "action_label": routing_result.action_label,
                            "concept_label": routing_result.concept_label,
                            "params": params,
                            "inferences": [
                                {
                                    "rule_label": inf.get("rule_label"),
                                    "description": inf.get("description"),
                                    "target": (
                                        f"{inf.get('target_concept')}.{inf.get('target_action')}()"
                                        if inf.get("target_action")
                                        else f"{inf.get('target_concept')}.{inf.get('target_property')} = {inf.get('target_value')}"
                                    ),
                                    "target_action": inf.get("target_action", ""),
                                    "target_params": inf.get("target_params", {}),
                                }
                                for inf in inferences
                            ],
                            "risk": "inference",
                        }
                        inf_confirm_event = self._prepare_confirmation(session_id)
                        yield ('confirm_required', _json.dumps(confirm_payload))
                        approved, _ = await self._wait_for_confirmation(session_id, timeout=None, event=inf_confirm_event)
                        yield ('confirm_result', _json.dumps({"approved": approved}))
                        if approved:
                            params['_confirmed_inferences'] = True
                        else:
                            params['_skip_inferences'] = True
                        tool_result = await action_executor.execute_structured_async(
                            routing_result.tool_name, params, user_id=user_id,
                        )
                        yield ('tool_result', _json.dumps({
                            "tool": routing_result.tool_name,
                            "rowCount": tool_result.get("rowCount", 0),
                            "source": tool_result.get("source", ""),
                            "sourceLabel": tool_result.get("sourceLabel", ""),
                        }))

                    # ── LLM format only ──
                    yield ('format_start', _json.dumps({}))

                    from app.core.prompts import FORMAT_ONLY_SYSTEM_PROMPT
                    tool_result_text = tool_result.get("result", "")
                    row_count = tool_result.get("rowCount", 0)

                    # 区分查询和写入操作，生成不同的格式化指令
                    if tool_result_text.startswith("创建成功") or tool_result_text.startswith("更新成功"):
                        format_message = (
                            f"### 操作结果\n{tool_result_text}\n\n"
                            f"### 用户消息\n{message}\n\n"
                            f"请将操作结果的所有字段以表格形式呈现给用户，第一列为字段名，第二列为值。"
                            f"必须列出结果中的每一项信息，不要省略任何字段。"
                        )
                    else:
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
                        "method": routing_result.method,
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

        # ── 未配置本体工具：fallback 或返回错误 ──
        if settings.AGENT_FALLBACK_ENABLED:
            log.info(f"[{self.name}] 无本体工具，进入 LLM Agent 兜底（无工具模式）")
            async for evt in self._llm_agent_fallback(
                message, session_id, model_name, enable_thinking,
                history_messages, None, user_id, concept_names=None,
            ):
                yield evt
            return

        yield ('content', f"该 Agent（{self.display_name}）未配置本体工具，无法处理请求。请联系管理员配置 ontology。")
        yield ('execution_done', _json.dumps({
            "totalSteps": 0, "error": "no_ontology_tools",
        }))

    async def _inject_data_filters(
        self, cypher: str, concept_names: list[str], user_id: str,
    ) -> tuple[str, dict]:
        """向 Cypher WHERE 子句注入 RBAC DataFilter 条件。

        只对 Cypher MATCH 中实际出现的概念注入过滤，
        匹配变量名→Label 映射，使用正确的变量注入条件。
        """
        import re as _re
        if not user_id or not concept_names:
            return cypher, {}

        from app.services.ontology_service import ontology_service
        from app.services.auth_service import auth_service as _auth_svc

        user_roles = await _auth_svc.get_effective_roles(user_id)
        if not user_roles:
            return cypher, {}

        # Parse MATCH clause: find all (var:Label) or (var:Label {...}) patterns
        label_vars: dict[str, str] = {}  # Label → var
        for m in _re.finditer(r'\((\w+):(\w+)', cypher):
            var_name = m.group(1)
            label = m.group(2)
            if label not in label_vars:
                label_vars[label] = var_name

        # Find which of the concept_names' Labels appear in the Cypher
        injected_params: dict[str, str] = {}
        idx = 0
        for name in concept_names:
            concept = ontology_service.get_concept(name)
            if not concept:
                continue
            c_label = concept.get("label", "")
            # Match concept to Cypher variable via Label mapping or MATCH label
            matching_label = None
            for neo4j_label, var in label_vars.items():
                if neo4j_label == name or neo4j_label == c_label:
                    matching_label = neo4j_label
                    break
            if not matching_label:
                continue  # This concept doesn't appear in the Cypher

            use_var = label_vars[matching_label]

            # Scope: 概念级图遍历范围（沿父链自动继承）
            scope = ontology_service.resolve_scope(name)
            if scope:
                scope_concept = scope["scopeConcept"]
                scope_prop = scope["scopeProperty"]
                scope_match = scope["scopeMatchProperty"]
                if scope_prop and scope_match:
                    dedup_key = f"_scope_{scope_concept}_{scope_prop}"
                    if not any(k.endswith(dedup_key) for k in injected_params):
                        user_val = await _auth_svc.get_user_property(user_id, scope_match)
                        if user_val is not None:
                            alias = f"__rbac_{idx}_{dedup_key}"
                            cypher = _inject_scope_clause(cypher, use_var, scope_concept, scope_prop, alias)
                            injected_params[alias] = user_val
                            idx += 1

            # DataFilter 规则：按角色的属性匹配
            for df in concept.get("dataFilters", []):
                roles = df.get("roles", [])
                if roles and not (user_roles & set(roles)):
                    continue

                prop = df.get("property", "")
                if not prop:
                    continue
                if any(k.endswith(f"_{prop}") for k in injected_params):
                    continue
                match_prop = df.get("matchProperty", "")
                user_val = await _auth_svc.get_user_property(user_id, match_prop)
                if user_val is not None:
                    alias = f"__rbac_{idx}_{prop}"
                    condition = f"{use_var}.{prop} = ${alias}"
                    cypher = _inject_where_clause(cypher, condition)
                    injected_params[alias] = user_val
                    idx += 1

        return cypher, injected_params

    async def _llm_agent_fallback(
        self, message: str, session_id: str, model_name: Optional[str],
        enable_thinking: Optional[bool], history_messages: Optional[List],
        onto_tools: Optional[list], user_id: str = "",
        concept_names: Optional[list[str]] = None,
    ) -> AsyncGenerator[tuple, None]:
        """LLM Agent fallback — 本体 Schema → LLM 生成 Cypher → 验证/注入/执行 → LLM 分析。

        L2 分类无匹配时进入。不使用 LLM function calling；而是把领域 Schema
        交给 LLM 生成只读 Cypher，系统验证安全后执行，再让 LLM 分析结果。
        """
        import json as _json
        import re as _re
        from app.services.llm_service import llm_service
        from app.services.neo4j_service import neo4j_service
        from app.services.ontology_service import ontology_service

        # ── Step 1: 获取全量本体 Schema（不限制于 action 概念，给 LLM 完整视角） ──
        schema_text = ontology_service.get_prompt()

        yield ('route_agent_fallback', _json.dumps({
            "agent": self.name,
            "concepts": concept_names or [],
            "schemaLength": len(schema_text) if schema_text else 0,
        }))

        if not schema_text:
            yield ('content', "当前本体未配置领域概念，请联系管理员完成本体建模。")
            yield ('execution_done', _json.dumps({"method": "cypher_fallback", "error": "no_schema"}))
            return

        # ── API 路由检查：概念配置了 API 则走业务系统直查 ──
        if concept_names:
            try:
                from app.services.multi_system_backend import multi_system_backend
                api_concepts = [c for c in concept_names if c in multi_system_backend._concept_system]
                if api_concepts:
                    # 可见步骤：告知前端正在走 API
                    yield ('content', f"已配置业务系统接口，直接查询: {', '.join(api_concepts)}")
                    log.info(f"[{self.name}] 兜底路径 API 路由: {api_concepts}")
                    results = []
                    for cn in api_concepts:
                        try:
                            r = await multi_system_backend.query(cn, {})
                            if r and "未找到" not in r and "失败" not in r:
                                results.append(r)
                        except Exception:
                            pass
                    if results:
                        combined = "\n\n".join(results)
                        yield ('tool_result', _json.dumps({
                            "tool": "ApiQuery", "rowCount": len(results), "source": "api",
                        }))
                        yield ('content', combined)
                        yield ('execution_done', _json.dumps({
                            "method": "api_routed", "rowCount": len(results),
                        }))
                        return
                    else:
                        # 检查是否允许降级
                        sys_cfg = multi_system_backend._systems.get(
                            multi_system_backend._concept_system.get(api_concepts[0], "")
                        )
                        if sys_cfg and not sys_cfg.fallback_on_error:
                            yield ('content', "业务系统查询无结果，已禁用降级，请检查接口配置。")
                            yield ('execution_done', _json.dumps({
                                "method": "api_routed", "rowCount": 0, "error": "fallback_disabled",
                            }))
                            return
                        yield ('content', "业务系统查询无结果，自动切换至图数据库补充查询")
                        log.info(f"[{self.name}] API 查询无结果，降级 Cypher 兜底")
                else:
                    yield ('content', f"未配置业务系统接口，使用图数据库查询")
            except Exception as e:
                sys_cfg = multi_system_backend._systems.get(
                    multi_system_backend._concept_system.get(concept_names[0] if concept_names else "", "")
                ) if concept_names else None
                if sys_cfg and not sys_cfg.fallback_on_error:
                    yield ('content', f"业务系统接口异常（{e}），已禁用降级，请检查接口配置。")
                    yield ('execution_done', _json.dumps({
                        "method": "api_routed", "rowCount": 0, "error": "fallback_disabled",
                    }))
                    return
                yield ('content', f"业务系统接口异常，自动切换至图数据库查询")
                log.warning(f"[{self.name}] API 路由检查失败: {e}")
        else:
            yield ('content', "使用图数据库查询")

        # ── Neo4j Label 映射 ──
        from app.services.ontology_service import ontology_service
        concepts = ontology_service.get_concepts()
        labels_text = "\n".join(
            f"- {c['name']} → `:{c['name']}`" for c in concepts
        ) if concepts else ""

        # ── Step 2-8: Cypher 生成 + 验证 + 执行（含重试） ──
        cypher_system_prompt = (
            "你是一个 Neo4j Cypher 查询专家。根据以下领域概念 Schema 和用户问题，"
            "生成一条只读 Cypher 查询。\n\n"
            f"## 领域 Schema\n{schema_text}\n\n"
            "属性格式: `propertyName(type): 中文标签`。Cypher 中用 `propertyName`。\n\n"
            "## Neo4j Label\n"
            f"{labels_text}\n\n"
            "## 关键：关系路径（来自 Schema 底部 \"关系路径\"）\n"
            "图中存在这些真实的关系边，你必须在查询中使用它们来做跨概念分析：\n"
            "- **遇到\"整体/全面/综合分析\"类问题时，禁止只查单一 Label，必须沿关系路径串联至少 2 个概念**\n"
            "- Schema 底部的\"关系路径\"就是可用的图遍历边\n"
            "- 遍历深度 1-3 跳\n\n"
            "## 聚合统计示例\n"
            "- \"各工单完成进度\" → MATCH (wo:WorkOrder)<-[:`所属工单`]-(t:WorkOrderTask) RETURN wo.code, sum(t.qty) AS 计划, sum(t.completedQty) AS 完工, round(sum(t.completedQty)/sum(t.qty)*100,1) AS 完成率\n"
            "- \"各工序任务数\" → MATCH (po:ProcessOperation)<-[:`对应工序`]-(t:WorkOrderTask) RETURN po.name, count(t) AS 任务数\n\n"
            "## 规则\n"
            "- 只输出一行 Cypher，不要 markdown 包裹\n"
            "- 只能 MATCH / RETURN / WHERE / ORDER BY / LIMIT / SKIP / WITH\n"
            "- 可用 sum/count/avg/round/min/max 做聚合统计\n"
            "- 必须含 LIMIT（最多 50）\n"
            "- 不要 RETURN *，用 AS 起中文别名\n"
            "- 字符串匹配用 CONTAINS，数值用 ="
        )

        MAX_RETRIES = 2
        cypher: str = ""
        params: dict = {}
        records: list[dict] = []
        # Cypher 生成需要较强的推理能力，忽略复杂度选择的模型
        cypher_model = "qwen-plus"

        for retry in range(MAX_RETRIES + 1):
            # ── Step 3: LLM 生成 Cypher ──
            cypher = ""
            async for t, c in llm_service.chat_stream(
                message=message, session_id=session_id,
                system_prompt=cypher_system_prompt,
                model_name=cypher_model,
                use_agent=False, web_search=False,
                history_messages=None,
                enable_thinking=False,  # Cypher 生成不需要深度思考
                tools=None,
            ):
                if t == 'content':
                    cypher += c

            cypher = cypher.strip()
            # 提取 markdown 代码块中的 Cypher
            code_match = _re.search(r'```(?:cypher|sql)?\s*\n?(.*?)```', cypher, _re.DOTALL | _re.IGNORECASE)
            if code_match:
                cypher = code_match.group(1).strip()

            log.info(f"[{self.name}] Cypher (retry={retry}): {cypher[:300]}")

            yield ('cypher_generation', _json.dumps({
                "cypher": cypher, "model": cypher_model, "retry": retry,
            }))

            # ── Step 4: 验证 ──
            valid, err_msg = neo4j_service.validate_readonly(cypher)
            if not valid:
                if retry < MAX_RETRIES:
                    cypher_system_prompt += f"\n\n【上一次的错误】{err_msg}。请修正后重新生成。"
                    continue
                yield ('content', f"Cypher 查询不安全：{err_msg}")
                yield ('execution_done', _json.dumps({"method": "cypher_fallback", "error": "unsafe_cypher"}))
                return

            # ── Step 5: RBAC DataFilter 注入 ──
            cypher, params = await self._inject_data_filters(
                cypher, concept_names or [], user_id,
            )
            if params:
                log.info(f"[{self.name}] RBAC injected: {params}")

            # ── Step 6: 执行 ──
            yield ('tool_start', _json.dumps({
                "tool": "CypherQuery", "params": {"cypher": cypher[:200]},
            }))
            try:
                records = await neo4j_service.execute_read(cypher, params) or []
            except Exception as e:
                log.error(f"[{self.name}] Cypher 执行失败 (retry={retry}): {e}")
                if retry < MAX_RETRIES:
                    cypher_system_prompt += (
                        f"\n\n【上一次执行错误】{e}。Cypher: {cypher}。请修正语法后重新生成。"
                    )
                    continue
                yield ('tool_result', _json.dumps({
                    "tool": "CypherQuery", "rowCount": 0, "source": "neo4j",
                    "error": str(e),
                }))
                yield ('content', "查询数据时发生错误，请稍后重试。")
                yield ('execution_done', _json.dumps({
                    "method": "cypher_fallback", "error": str(e),
                }))
                return

            # Success — exit retry loop
            break

        # 列级数据过滤：根据用户角色限制可见属性
        if user_id and records:
            from app.services.action_executor import apply_column_filters
            from app.services.auth_service import auth_service as _auth_svc
            user_roles = await _auth_svc.get_effective_roles(user_id)
            if user_roles:
                from app.services.ontology_service import ontology_service
                for cname in (concept_names or []):
                    concept = ontology_service.get_concept(cname)
                    if concept:
                        records = apply_column_filters(concept, user_roles, records)

        yield ('tool_result', _json.dumps({
            "tool": "CypherQuery", "rowCount": len(records), "source": "neo4j",
        }))

        # ── Step 7: LLM 分析结果 ──
        yield ('format_start', _json.dumps({}))

        from app.core.prompts import FORMAT_ONLY_SYSTEM_PROMPT
        system_prompt = await self.build_system_prompt(include_tools_prompt=False)
        analysis_system = f"{FORMAT_ONLY_SYSTEM_PROMPT}\n\n{system_prompt}"

        # 截断大数据集
        MAX_RESULT_CHARS = 4000
        results_json = _json.dumps(records, ensure_ascii=False, default=str)
        if len(results_json) > MAX_RESULT_CHARS:
            results_json = results_json[:MAX_RESULT_CHARS] + f"\n… (共 {len(records)} 条，已截断前 {MAX_RESULT_CHARS} 字符)"

        analysis_message = (
            f"## 领域 Schema\n{schema_text}\n\n"
            f"## 查询结果（共 {len(records)} 条）\n{results_json}\n\n"
            f"## 用户问题\n{message}\n\n"
            f"请基于以上 Schema 和数据，用自然语言回答用户问题，给出专业分析。"
        )

        try:
            async for t, c in llm_service.chat_stream(
                message=analysis_message, session_id=session_id,
                system_prompt=analysis_system,
                model_name=model_name,
                use_agent=False, web_search=False,
                history_messages=history_messages,
                enable_thinking=enable_thinking,
                tools=None,
            ):
                if t == 'content':
                    yield ('content', c)

            yield ('execution_done', _json.dumps({
                "method": "cypher_fallback", "rowCount": len(records),
            }))
        except Exception as e:
            log.error(f"[{self.name}] fallback analysis error: {e}", exc_info=True)
            yield ('content', "处理请求时发生错误，请稍后重试。")
            yield ('execution_done', _json.dumps({
                "totalSteps": 0, "error": str(e),
            }))

    async def call_tools(self, message: str) -> Optional[str]:
        """调用领域工具，返回格式化结果文本"""
        return None

    async def _call_tools_via_ontology(self, message: str, user_id: str = "") -> Optional[str]:
        """通过本体链路执行工具：L2 语义分类 + 参数提取 + 执行。

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

            # L2 LLM semantic classification (same as _standard_process)
            candidates = intent_router.get_candidates(self.name)
            candidate_list = [
                {
                    "name": fn,
                    "label": e.action_label,
                    "description": e.description,
                    "concept_label": e.concept_label,
                }
                for fn, e in candidates.items()
            ]
            tool_name = None

            if candidate_list:
                tool_name = await self._llm_classify_action(message, candidate_list)

            if not tool_name and candidates:
                # Fallback: simple concept_label matching for query actions
                queries = {k: v for k, v in candidates.items() if '_query' in k}
                if queries:
                    best_score = -1
                    for k, v in queries.items():
                        score = 1 if v.concept_label in message else 0
                        if score > best_score:
                            best_score = score
                            tool_name = k
                    if not tool_name:
                        tool_name = list(queries.keys())[0]
                elif candidates:
                    tool_name = list(candidates.keys())[0]

            if not tool_name:
                return None

            params = intent_router.extract_params(message, tool_name)
            tool_result = await action_executor.execute_structured_async(tool_name, params, user_id=user_id)
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
        """自检响应质量 — 本体架构中 LLM 仅做格式化，默认不做强制修正。
        子类可覆盖此方法添加领域特定的校验逻辑（如排产结果必须含产线信息）。"""
        if not response or len(response.strip()) < 5:
            return None
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
