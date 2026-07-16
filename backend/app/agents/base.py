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

    async def _rag_recall_skills(self, message: str, candidates: list) -> list:
        """用 embedding 向量相似度从候选 Skill 中召回 Top-5。"""
        import json, math
        from app.core.config import settings

        if not settings.DASHSCOPE_API_KEY:
            return candidates

        try:
            from langchain_community.embeddings import DashScopeEmbeddings
            emb = DashScopeEmbeddings(model="text-embedding-v3", dashscope_api_key=settings.DASHSCOPE_API_KEY)
            query_vec = await asyncio.to_thread(emb.embed_query, message)
        except Exception as e:
            log.warning(f"[RAG recall] query embedding failed: {e}")
            return candidates

        try:
            from app.db import get_db
            from sqlalchemy import select
            from app.models.skill_embedding import SkillEmbedding
            async for session in get_db():
                r = await session.execute(select(SkillEmbedding.skill_name, SkillEmbedding.embedding))
                rows = {row[0]: json.loads(row[1]) for row in r.fetchall() if row[1]}
                break
        except Exception as e:
            return candidates

        # 首次无数据 → 从 candidates 生成
        if not rows and candidates:
            try:
                from app.models.skill_embedding import SkillEmbedding
                texts = [f"{c.get('label','')} {c.get('concept_label','')} {c.get('description','')}" for c in candidates]
                names_c = [c['name'] for c in candidates]
                vecs = await asyncio.to_thread(emb.embed_documents, texts)
                async for session in get_db():
                    for n, v in zip(names_c, vecs):
                        se = SkillEmbedding(skill_name=n, embedding=json.dumps(v))
                        await session.merge(se)
                    await session.commit()
                rows = {n: v for n, v in zip(names_c, vecs)}
                log.info(f"[RAG recall] 首次生成 {len(names_c)} Skill embeddings")
            except Exception as e:
                log.warning(f"[RAG recall] 生成embedding失败: {e}")
                return candidates

        if not rows:
            return candidates

        def cosine(a, b):
            dot = sum(x*y for x,y in zip(a,b))
            na = math.sqrt(sum(x*x for x in a))
            nb = math.sqrt(sum(y*y for y in b))
            return dot/(na*nb) if na and nb else 0

        scores = []
        for c in candidates:
            vec = rows.get(c['name'])
            if vec:
                scores.append((cosine(query_vec, vec), c))
        scores.sort(key=lambda x: x[0], reverse=True)

        top5 = [c for _, c in scores[:5]]
        return top5 if len(top5) >= 5 else candidates

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
            f"你是一个{domain_desc}领域的意图分类器。\n"
            "规则：\n"
            "1. 用户明确提到具体的业务对象（工单、设备、质检、物料等）→ 匹配对应操作\n"
            "2. 用户问题模糊、泛指（如整体情况怎样、最近如何）→ 坚决返回 NONE\n"
            "3. 宁可漏过十个模糊查询，不可错配一个具体操作\n"
            "只返回操作名或 NONE：\n\n"
            f"可选操作（按概念域分组）：\n{options}\n\n"
            f"用户消息：{message}\n\n"
            "最匹配的操作名称（NONE 或具体操作名）："
        )

        known = {c['name'] for c in candidates}

        # 触发词优先匹配 — 用户配置的触发词直接命中，跳过 LLM
        msg_lower = message.lower().strip()
        matched = []
        for c in candidates:
            concept_name = c.get('concept_name', '')
            action_name = c.get('name', '')
            is_create = '_create' in action_name or '_add' in action_name
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
            best = matched[0]
            log.info(f"[L2 Classify] trigger match: '{message}' -> {best[0]} (trigger='{best[1]}')")
            return best[0]

        # ── RAG 意图召回：embedding 向量检索 Top-5 候选 ──
        if len(candidates) > 10:
            try:
                top5 = await self._rag_recall_skills(message, candidates)
                if top5 and len(top5) < len(candidates):
                    log.info(f"[L2 Classify] RAG recall: {len(candidates)}→{len(top5)} candidates")
                    candidates = top5
            except Exception as e:
                log.warning(f"[L2 Classify] RAG recall failed: {e}")

        try:
            classify_model = model_name or "qwen-turbo"  # 和会话一致
            result = await asyncio.wait_for(
                llm_service.chat_sync(
                    message=classify_prompt,
                    system_prompt="意图分类器。用户没提具体对象（工单/设备/质检等）时坚决返回NONE。只有明确提到具体对象时才匹配。宁漏不误。",
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
            log.warning(f"[L2 Classify] timeout (8s) for {len(candidates)} candidates")
        except Exception as e:
            log.warning(f"[L2 Classify] failed: {e}")

        # LLM 分类失败 → 返回 None，由上层追问或走 L3
        log.warning(f"[L2 Classify] no LLM match for '{message}'")
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
        import re as _re
        from app.services.llm_service import llm_service

        # 多轮意图：上一条是 ASK 追问 → 跳过 L2，直接动态规划
        _is_ask_followup = False
        if history_messages:
            last_agent = None
            for hm in reversed(history_messages):
                role = getattr(hm, 'type', '') or getattr(hm, 'role', '')
                if role in ('ai', 'assistant', 'agent'):
                    last_agent = str(getattr(hm, 'content', ''))
                    break
            if last_agent and ('哪方面' in last_agent or '具体指' in last_agent or '请确认' in last_agent or '需要确认' in last_agent):
                _is_ask_followup = True
                log.info(f"[{self.name}] 检测到ASK追问的回复: {message[:50]}")

        if _is_ask_followup:
            try:
                from app.core.chain_engine import chain_engine as _ce3
                if _ce3._get_compiled_runtime():
                    async for evt_type, evt_data in _ce3._execute_dynamic(
                        message=message, model_name=model_name,
                        enable_thinking=enable_thinking, session_id=session_id,
                        history_messages=history_messages,
                    ):
                        if evt_type == 'error': break
                        yield (evt_type, evt_data)
                    else:
                        yield ('execution_done', _json.dumps({"method": "dynamic_plan"}))
                        return
            except Exception as e:
                log.warning(f"[{self.name}] ASK追问→动态规划失败: {e}")

        # 反馈闭环：用户说不是/不对/取消 → 记录为纠正信号
        _is_correction = _re.search(r'^不是|^不对|^取消|^搞错了|^我.*不是', message.strip())
        if _is_correction:
            log.info(f"[{self.name}] 用户纠正信号: {message[:50]}")

        # 短消息拼接上文关键信息（精简，避免干扰L2路由）
        _short_message = len(message.strip()) < 15
        if _short_message and history_messages:
            # 只提取最近2条用户消息作为上下文，不拼全量历史
            user_msgs = []
            for hm in reversed(history_messages):
                role = getattr(hm, 'type', '') or getattr(hm, 'role', '')
                if role in ('user', 'human'):
                    content = str(getattr(hm, 'content', ''))[:150]
                    if content and content != message.strip():
                        user_msgs.insert(0, content)
                if len(user_msgs) >= 2:
                    break
            if user_msgs:
                context = "；".join(user_msgs)
                message = f"上文：{context}。当前问题：{message}"
                log.info(f"[{self.name}] 短消息拼接上下文: {message[:200]}")

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
        # 歧义检测已由外层 process_message_stream 统一处理
        if onto_tools:
            try:
                from app.services.intent_router import intent_router, RoutingResult
                from app.services.action_executor import action_executor

                if not intent_router.ready:
                    intent_router.rebuild(ontology_service, action_executor)

                if intent_router.ready:
                    # L2 静默分类（不先发事件，等确定模式后再发）
                    candidates = intent_router.get_candidates(self.name)
                    candidate_list = [
                        {"name": fn, "label": e.action_label, "description": e.description,
                         "concept_label": e.concept_label, "concept_name": e.concept_name}
                        for fn, e in candidates.items()
                    ]
                    concept_names = list(dict.fromkeys(
                        c["concept_name"] for c in candidate_list if c.get("concept_name")
                    )) if candidate_list else []

                    l2_name = None
                    if candidate_list:
                        l2_name = await self._llm_classify_action(
                            message, candidate_list, model_name,
                        )

                    if l2_name:
                        # ── 工具匹配：发竖向步骤 ──
                        yield ('route_start', _json.dumps({
                            "agent": self.name, "display_name": self.display_name,
                            "message": message[:100],
                        }))
                        yield ('route_l2', _json.dumps({
                            "candidateCount": len(candidate_list),
                            "concepts": concept_names,
                        }))
                        routing_result = intent_router.route_explicit(l2_name, message)
                    else:
                        # ── 无工具匹配：尝试动态规划 ──
                        try:
                            from app.core.chain_engine import chain_engine as _ce2
                            if _ce2._get_compiled_runtime():
                                log.info(f"[{self.name}] L3 → 动态规划")
                                async for evt_type, evt_data in _ce2._execute_dynamic(
                                    message=message, model_name=model_name,
                                    enable_thinking=enable_thinking, session_id=session_id,
                                ):
                                    if evt_type == 'error':
                                        log.warning(f"[{self.name}] 动态规划失败: {evt_data}")
                                        break
                                    yield (evt_type, evt_data)
                                else:
                                    yield ('execution_done', _json.dumps({"method": "dynamic_plan"}))
                                    return
                        except Exception as e:
                            log.warning(f"[{self.name}] 动态规划异常: {e}")

                    # L2 返回 NONE → 轻量追问，不走 L3
                    if not l2_name and candidate_list:
                        # 列出 Top-3 候选域给用户参考
                        domains = list(dict.fromkeys(c.get("concept_label", "其他") for c in candidate_list[:10]))[:3]
                        domain_hint = "、".join(domains)
                        yield ('content', f"您想了解哪方面？比如：{domain_hint}等。请再描述一下具体需求。")
                        yield ('execution_done', _json.dumps({"method": "clarify"}))
                        return

                    # L3: no L2 match and dynamic failed/unavailable → fallback
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

                    # ── 约束规则审批门禁（必须在违规判断之前）──
                    approvals = tool_result.get("approvals", [])
                    if tool_result.get("needs_approval") and approvals:
                        approval_roles = set()
                        for a in approvals:
                            for r in (a.get("approval_roles") or []):
                                approval_roles.add(r)
                        rule_labels = "、".join(a.get("rule_label", "") for a in approvals)

                        if approval_roles:
                            # 检查用户角色
                            from app.services.auth_service import auth_service as _auth_svc
                            _user_roles = await _auth_svc.get_effective_roles(user_id) if user_id else set()
                            needs_delegate = not (_user_roles & approval_roles)

                            if needs_delegate:
                                assigned = list(approval_roles)
                                yield ('confirm_delegated', _json.dumps({
                                    "tool": routing_result.tool_name,
                                    "action_label": f"{routing_result.action_label}（规则审批: {rule_labels}）",
                                    "concept_label": routing_result.concept_label,
                                    "params": params,
                                    "param_schema": await intent_router.get_param_schema(routing_result.tool_name),
                                    "risk": "write",
                                    "assigned_to": assigned,
                                    "context": {"rule_approval": rule_labels},
                                }))
                                yield ('content', f"已确认操作，但因规则「{rule_labels}」需要 **{assigned[0]}** 审批。已提交待办。")
                            else:
                                yield ('content', f"规则「{rule_labels}」触发审批。因您具有审批权限，请确认后执行。")
                                # 内联确认
                                _approve_event = self._prepare_confirmation(session_id)
                                yield ('confirm_required', _json.dumps({
                                    "tool": routing_result.tool_name,
                                    "action_label": f"{routing_result.action_label}（规则审批: {rule_labels}）",
                                    "concept_label": routing_result.concept_label,
                                    "params": params,
                                    "param_schema": await intent_router.get_param_schema(routing_result.tool_name),
                                    "risk": "rule_approval",
                                    "context": {"rule_approval": rule_labels},
                                }))
                                _approved, _ = await self._wait_for_confirmation(session_id, timeout=None, event=_approve_event)
                                if not _approved:
                                    yield ('content', "操作已取消。")
                                    yield ('execution_done', _json.dumps({"totalSteps": 4, "cancelled": True}))
                                    return
                                # 重新执行
                                tool_result = await action_executor.execute_structured_async(
                                    routing_result.tool_name, params, user_id=user_id,
                                )
                        else:
                            yield ('content', f"操作被规则「{rule_labels}」拦截（规则未配置审批角色，无法提交审批）。")
                        yield ('execution_done', _json.dumps({
                            "totalSteps": 4, "cancelled": True,
                            "delegated": True if (approval_roles and needs_delegate) else None,
                        }))
                        return

                    # Rule violation: 回到确认表单让用户修正参数
                    if tool_result.get("source") == "rule_engine" and not tool_result.get("needs_approval"):
                        yield ('rule_violation', tool_result.get("result", ""))
                        # 重新弹出确认表单，保留已填参数
                        yield ('confirm_required', _json.dumps({
                            "tool": routing_result.tool_name,
                            "action_label": routing_result.action_label,
                            "concept_label": routing_result.concept_label,
                            "params": params,
                            "param_schema": await intent_router.get_param_schema(routing_result.tool_name),
                            "risk": "write",
                            "context": {"violation": tool_result.get("result", "")},
                        }))
                        approved, retry_params = await self._wait_for_confirmation(session_id, timeout=None, event=self._prepare_confirmation(session_id))
                        yield ('confirm_result', _json.dumps({"approved": approved, "params": retry_params}))
                        if not approved:
                            yield ('content', "操作已取消。")
                            yield ('execution_done', _json.dumps({"totalSteps": 4, "cancelled": True}))
                            return
                        # 用修正后的参数重试
                        params = {**params, **(retry_params or {})}
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
                        # 如果修正后仍有违规，不再循环，直接提示
                        if tool_result.get("source") == "rule_engine" and not tool_result.get("needs_approval"):
                            yield ('rule_violation', tool_result.get("result", ""))
                            yield ('content', tool_result.get("result", ""))
                            yield ('execution_done', _json.dumps({"totalSteps": 4, "cancelled": True}))
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
                        # 获取概念属性中文标签映射
                        prop_labels = {}
                        try:
                            tool_name = routing_result.tool_name if routing_result else ''
                            concept_name = tool_name.split('_')[0] if '_' in tool_name else tool_name
                            from app.services.ontology_service import ontology_service
                            concept = ontology_service.get_concept(concept_name)
                            if concept:
                                for p in concept.get('properties', []):
                                    name = p.get('name', '')
                                    label = p.get('label', '') or name
                                    if name:
                                        prop_labels[name] = label
                                        # 也加 Display 后缀的
                                        prop_labels[f'{name}Display'] = label
                        except Exception:
                            pass
                        label_hint = '\n'.join(f'{k}→{v}' for k, v in prop_labels.items()) if prop_labels else ''
                        format_message = (
                            (f"### 字段中文名\n{label_hint}\n\n" if label_hint else '') +
                            f"### 查询结果\n{tool_result_text}\n\n"
                            f"### 用户消息\n{message}\n\n"
                            f"请基于查询结果回复。表格列标题用上面提供的中文名替换英文字段名，"
                            f"如 routingCodeDisplay→工艺路线，id→编号。"
                            f"没有映射的字段保持不变。"
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
                        log.info(f"[{self.name}] API 查询无结果，降级 Cypher 兜底")
                else:
                    log.info(f"[{self.name}] 未配置业务系统接口，使用图数据库查询")
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
        from datetime import datetime as _dt3
        _today3 = _dt3.now().strftime("%Y-%m-%d")
        _has_exact_time = _re.search(r'今天|今日|昨天|明天|当前|现在', message)
        _has_range_time = _re.search(r'最近|本周|本月|近.*月|近.*天|近.*年|今年以来', message)
        if _has_exact_time:
            _time_rule = f"【当前日期: {_today3}。用户问具体某天，WHERE 用 = date() 精确过滤。】"
        elif _has_range_time:
            _time_rule = f"【当前日期: {_today3}。用户问时间段，用日期范围过滤如 date(xxx) >= date() - duration(...)。】"
        else:
            _time_rule = "【用户未提到具体时间，不要加任何日期过滤条件。】"
        cypher_system_prompt = (
            _time_rule +
            "你是一个 Neo4j Cypher 查询专家。根据领域概念 Schema 和用户问题，"
            "制定分析计划并生成查询。\n\n"
            f"## 领域 Schema\n{schema_text}\n\n"
            "属性格式: `propertyName(type): 中文标签`。Cypher 中用 `propertyName`。\n\n"
            "## Neo4j Label\n"
            f"{labels_text}\n\n"
            "## 关键：关系路径（来自 Schema 底部 \"关系路径\"）\n"
            "图中存在这些真实的关系边，你可以通过它们做跨概念关联分析：\n"
            "- Schema 底部的\"关系路径\"就是可用的图遍历边\n"
            "- 遍历深度 1-3 跳\n"
            "- 遇到综合分析类问题，利用关系路径关联多张概念表\n\n"
            "## 工作流程\n"
            "1. 看用户问题涉及的领域，从 Schema 中识别 1-3 个核心概念\n"
            "2. 如果有关系路径连接，生成跨概念查询（用关系边 JOIN）\n"
            "3. 输出格式：先写查询计划（一行简述），再写 Cypher 查询\n\n"
            "## 规则\n"
            "- 输出一行 Cypher，不要 markdown 包裹\n"
            "- 可用 MATCH / OPTIONAL MATCH / RETURN / WHERE / ORDER BY / LIMIT / WITH\n"
            "- 可用 sum/count/avg/round/min/max 做聚合统计\n"
            "- 必须含 LIMIT（最多 50）\n"
            "- 关系名和标签名有中文或特殊字符必须用反引号包裹\n"
            "- **用户提到\"今天/今日/最近\"等时间词，WHERE 中必须加日期过滤**"
            "（WHERE date(r.startDate) = date() ）\n"
            "- 查询结果为空就如实说，不要编造数据\n"
            "- WHERE 条件不要过于严格，避免过滤掉有效数据\n"
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
                history_messages=history_messages,
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

        from app.core.prompts import CYPHER_ANALYSIS_SYSTEM_PROMPT
        system_prompt = await self.build_system_prompt(include_tools_prompt=False)
        analysis_system = f"{CYPHER_ANALYSIS_SYSTEM_PROMPT}\n\n{system_prompt}"

        # 截断大数据集
        MAX_RESULT_CHARS = 4000
        results_json = _json.dumps(records, ensure_ascii=False, default=str)
        if len(results_json) > MAX_RESULT_CHARS:
            results_json = results_json[:MAX_RESULT_CHARS] + f"\n… (共 {len(records)} 条，已截断前 {MAX_RESULT_CHARS} 字符)"

        _date_hint = (f"【当前日期: {_today3}。报告日期写 {_today3}。无 {_today3} 数据就说无数据\n\n"
                      if (_has_exact_time or _has_range_time) else "")
        analysis_message = (
            _date_hint +
            f"## 本体 Schema（含概念和关系路径）\n{schema_text}\n\n"
            f"## 查询结果（共 {len(records)} 条）\n{results_json}\n\n"
            f"## 用户问题\n{message}\n\n"
            f"根据查询结果和本体 Schema，进行分析：\n"
            f"1. 关键指标用 Markdown 表格展示，**只包含查询结果中实际存在的数据**\n"
            f"2. 遇到以下情况必须反问用户，不要自行猜测：\n"
            f"   - 时间范围不明确（如最近、前段时间）\n"
            f"   - 查询对象不明确（如那个工单）\n"
            f"   - 指标定义模糊（如生产效率无具体算法）\n"
            f"3. 如果查询结果涉及多个概念，利用 Schema 关系路径做关联分析\n"
            f"4. 有数据就生成图表，没数据不编造\n"
            f"5. 根据结论给出简要行动建议"
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
                tool_name = await self._llm_classify_action(message, candidate_list, model_name)

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
