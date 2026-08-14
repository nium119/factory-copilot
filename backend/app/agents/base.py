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
    namespace: str = ""  # 业务域 namespace，查询时自动用

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

    def _prepare_confirmation(self, session_id: str, message: str = "", action_label: str = "") -> asyncio.Event:
        """Register a pending confirmation BEFORE yielding confirm_required."""
        event = asyncio.Event()
        entry = {"event": event, "approved": False, "params": {}, "message": message, "action_label": action_label}
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

    # ── embedding 内存缓存 ──
    _embedding_cache: dict = {}       # {namespace: {skill_name: [float]}}
    _embedding_cache_ts: float = 0

    @classmethod
    def invalidate_embedding_cache(cls):
        cls._embedding_cache = {}
        cls._embedding_cache_ts = 0

    async def _load_embedding_cache(self, namespace: str) -> dict:
        """从 DB 加载指定 namespace 的 embedding 到内存缓存。"""
        import json
        import time
        now = time.time()
        if namespace in self._embedding_cache and now - self._embedding_cache_ts < 300:
            return self._embedding_cache[namespace]
        try:
            from sqlalchemy import select

            from app.db import get_db
            from app.models.skill_embedding import SkillEmbedding
            async for session in get_db():
                r = await session.execute(
                    select(SkillEmbedding.skill_name, SkillEmbedding.embedding)
                    .where(SkillEmbedding.namespace == namespace)
                )
                rows = {row[0]: json.loads(row[1]) for row in r.fetchall() if row[1]}
                break
            self._embedding_cache[namespace] = rows
            self._embedding_cache_ts = now
            return rows
        except Exception:
            return self._embedding_cache.get(namespace, {})

    # ── RAG 统计追踪（持久化到 DB）──
    _rag_stats = None  # 延迟加载
    _rag_stats_lock = None

    @classmethod
    async def _load_rag_stats(cls) -> dict:
        """从 DB 加载 RAG 统计，首次调用时初始化。"""
        if cls._rag_stats is not None:
            return cls._rag_stats
        default = {"total": 0, "hit": 0, "miss": 0, "fallback": 0, "avg_max_sim": 0.0, "mode": {"vec": 0, "bm25": 0, "hybrid": 0, "fallback": 0}}
        try:
            from app.db import get_db
            async for session in get_db():
                from app.repositories.namespace_config_repo import NamespaceConfigRepository
                repo = NamespaceConfigRepository(session)
                saved = await repo.get("_system", "rag_stats")
                cls._rag_stats = {**default, **saved} if saved else dict(default)
                break
        except Exception:
            cls._rag_stats = dict(default)
        return cls._rag_stats

    @classmethod
    async def _save_rag_stats(cls):
        """保存 RAG 统计到 DB。"""
        try:
            from app.db import get_db
            async for session in get_db():
                from app.repositories.namespace_config_repo import NamespaceConfigRepository
                repo = NamespaceConfigRepository(session)
                await repo.save("_system", "rag_stats", cls._rag_stats or {})
                break
        except Exception:
            pass

    @classmethod
    async def _record_rag(cls, hit: bool, max_sim: float, mode: str):
        """记录一次 RAG 召回结果。"""
        import asyncio
        s = await cls._load_rag_stats()
        s["total"] += 1
        if hit:
            s["hit"] += 1
            s["avg_max_sim"] = (s["avg_max_sim"] * (s["hit"] - 1) + max_sim) / s["hit"]
        else:
            s["miss"] += 1
        s["mode"][mode] = s["mode"].get(mode, 0) + 1
        asyncio.create_task(cls._save_rag_stats())

    @classmethod
    async def get_rag_stats(cls) -> dict:
        return dict(await cls._load_rag_stats())

    # 多重 embedding 权重: label 30% + concept 30% + description 40%
    _EMBED_WEIGHTS = [0.3, 0.3, 0.4]

    async def _bm25_search(self, message: str, namespace: str) -> dict:
        """BM25 关键词检索，返回 {skill_name: score}。"""
        try:
            import sqlalchemy as sa

            from app.db import get_db
            async for session in get_db():
                r = await session.execute(
                    sa.text("SELECT skill_name, bm25(agent_skill_fts, 0.0, 1.0, 10.0) AS score "
                            "FROM agent_skill_fts WHERE namespace = :ns AND agent_skill_fts MATCH :q "
                            "ORDER BY score"),
                    {"ns": namespace, "q": message})
                rows = r.fetchall()
                break
            return {row[0]: row[1] for row in rows if row[1] is not None}
        except Exception as e:
            log.warning(f"[BM25] search failed: {e}")
            return {}

    async def _rag_recall_skills(self, message: str, candidates: list) -> list:
        """向量 + BM25 混合召回：加权融合相似度。"""
        import math

        from app.core.model_config import create_embedding

        SIM_THRESHOLD = 0.5
        MIN_CANDIDATES = 5
        VEC_WEIGHT = 0.6      # 向量权重（语义）
        BM25_WEIGHT = 0.4     # 关键词权重（精确匹配）
        from app.services.ontology_service import ontology_service
        namespace = ontology_service.active_namespace

        # 检查 BM25 是否启用
        try:
            from app.api.model_config import DEFAULT_SELECTION, _load_config
            cfg = await _load_config() or {}
        except Exception:
            cfg = {}
        sel = cfg.get("selection", {})
        enable_bm25 = sel.get("enable_bm25", DEFAULT_SELECTION["enable_bm25"])

        # 1. 向量召回
        vec_scores = {}
        try:
            emb = create_embedding()
            if emb:
                query_vec = await asyncio.to_thread(emb.embed_query, message)
                rows = await self._load_embedding_cache(namespace)
                if rows:

                    def cosine(a, b):
                        dot = sum(x*y for x,y in zip(a,b))
                        na = math.sqrt(sum(x*x for x in a))
                        nb = math.sqrt(sum(y*y for y in b))
                        return dot/(na*nb) if na and nb else 0

                    for c in candidates:
                        total = 0.0
                        ws = 0.0
                        for suffix, w in zip(['_label', '_concept', '_desc'], self._EMBED_WEIGHTS):
                            v = rows.get(f"{c['name']}{suffix}")
                            if v:
                                total += cosine(query_vec, v) * w
                                ws += w
                        if ws > 0:
                            vec_scores[c['name']] = total / ws
                        else:
                            v = rows.get(c['name'])
                            if v:
                                vec_scores[c['name']] = cosine(query_vec, v)
        except Exception as e:
            log.warning(f"[RAG] vector recall failed: {e}")

        # 2. BM25 关键词召回
        bm25_scores = {}
        if enable_bm25:
            bm25_scores = await self._bm25_search(message, namespace)

        # 3. 加权合并
        has_vec = len(vec_scores) > 0
        has_bm25 = len(bm25_scores) > 0

        combined = []
        candidates_by_name = {c['name']: c for c in candidates}
        for name, c in candidates_by_name.items():
            score = 0.0
            if has_vec and has_bm25:
                score = VEC_WEIGHT * vec_scores.get(name, 0) + BM25_WEIGHT * bm25_scores.get(name, 0)
            elif has_vec:
                score = vec_scores.get(name, 0)
            elif has_bm25:
                score = bm25_scores.get(name, 0)
            else:
                continue  # 都没有 → 退化到全量 LLM 分类

            if score >= SIM_THRESHOLD:
                combined.append((score, c))

        has_any = has_vec or has_bm25
        mode = "hybrid" if has_vec and has_bm25 else ("vec" if has_vec else ("bm25" if has_bm25 else "none"))
        if not has_any:
            self._record_rag(False, 0.0, "fallback")
            return candidates
        if len(combined) < MIN_CANDIDATES:
            self._record_rag(False, combined[0][0] if combined else 0.0, mode)
            return candidates

        combined.sort(key=lambda x: x[0], reverse=True)
        top5 = [c for _, c in combined[:5]]
        self._record_rag(True, combined[0][0], mode)
        log.info(f"[RAG] {len(candidates)}→{len(top5)} (vec={len(vec_scores)}, bm25={len(bm25_scores)}, mode={mode})")
        return top5

    def _trigger_match(self, message: str, candidates: list) -> tuple:
        """触发词匹配。返回 (action_name, 'trigger') 或 (None, 'llm')。"""
        msg_lower = message.lower().strip()
        # 超短消息（≤2 字，通常是澄清回答/片段）不做触发词匹配，避免"时间"误配 get_current_time
        if len(msg_lower) <= 2:
            return None, "llm", 0.0
        matched = []
        # 补充 MCP 工具候选（绕过 agent 过滤链）
        try:
            from app.mcp import mcp_registry
            for tool_name in mcp_registry.get_tool_names():
                if not any(c.get('name') == tool_name for c in candidates):
                    ov = _cached_mcp_overrides.get(tool_name, {})
                    candidates = list(candidates) + [{
                        'name': tool_name,
                        'label': ov.get('label', tool_name),
                        'concept_name': tool_name,
                        'concept_label': ov.get('label', tool_name),
                    }]
        except Exception:
            pass
        for c in candidates:
            concept_name = c.get('concept_name', '')
            action_name = c.get('name', '')
            is_create = '_create' in action_name or '_add' in action_name
            try:
                from app.services.intent_router import _load_skill_triggers
                triggers = _load_skill_triggers(action_name) or []
                # MCP 工具从缓存取全局触发词
                if action_name.startswith('mcp_'):
                    ov = _cached_mcp_overrides.get(action_name, {})
                    if ov.get('triggers'):
                        triggers = list(set(triggers + ov['triggers']))
                if is_create:
                    triggers += _load_skill_triggers(f"{concept_name}_query") or []
                best_t = ""
                for t in triggers:
                    # 触发词只信用户配置（SkillsTab 手动添加），子串匹配（配置时语义已确认）
                    if t and (t in msg_lower or msg_lower in t):
                        if len(t) > len(best_t):
                            best_t = t
                if best_t:
                    matched.append((action_name, best_t, is_create))
            except Exception:
                pass

        if matched:
            exact = [m for m in matched if m[1] == msg_lower]
            if exact:
                best = exact[0]
            else:
                best = max(matched, key=lambda m: len(m[1]))
            log.info(f"[Trigger] '{message}' -> {best[0]} (trigger='{best[1]}')")
            return best[0], "trigger", 1.0
        return None, "llm", 0.0

    async def _llm_classify_action(
        self, message: str, candidates: list, model_name: Optional[str],
        rag_used: bool = False,
    ) -> tuple:
        """L2 LLM classification. Returns (fn_name_or_None, method, confidence)."""
        import json as _json_l2

        from app.services.llm_service import llm_service

        if not candidates:
            return None, "llm", 0.0

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
            "1. 判断用户意图类型：想改变系统状态→操作类，想获取信息→分析类\n"
            "2. 操作类意图在可选列表中无对应项→返回 UNSUPPORTED\n"
            "3. 分析类意图无精确匹配→返回 NONE\n"
            "4. 明确提到业务对象→匹配对应操作；模糊泛指→返回 NONE\n"
            "5. 宁可漏过十个模糊查询，不可错配一个具体操作\n"
            "6. 改写动词默认不可降级匹配（→ UNSUPPORTED）：\n"
            "   更新/修改/编辑/调整/变更 → 不是创建/删除\n"
            "   禁用/启用/分配/指派/转移 → 不是修改\n"
            "   导入/导出/备份/恢复 → 不是查询\n"
            "   例外：含[影响/后果/关联/依赖/会怎样]等分析词 → 跳过 UNSUPPORTED，返回 NONE（让系统走多跳分析）\n"
            "7. 仅当用户使用的动词与操作名确切匹配时才选中\n"
            "   如：创建→create, 删除→delete, 查询→query\n"
            "8. 闲聊/寒暄/讨论类输入（问候、感谢、征求意见、纯知识讨论，无明确数据查询或操作意图）→ 返回 CHAT\n"
            "9. 「概念+字段」表述（如「工单状态」「工单数量」「设备状态」）中，字段是概念的属性，应匹配到该概念（「工单状态」→查询工单），不要当成独立查询对象\n"
            "10. 「最小/最大/最新/最早/最少/最多」是排序/聚合修饰词，不影响概念匹配（「最小的工单」→工单），修饰词交给后续参数提取处理\n"
            "返回JSON格式：{\"action\":\"操作名或NONE或UNSUPPORTED或CHAT\",\"confidence\":0.0~1.0}\n\n"
            f"可选操作（按概念域分组）：\n{options}\n\n"
            f"用户消息：{message}\n\n"
            "最匹配的操作（JSON格式）："
        )

        known = {c['name'] for c in candidates}

        # ── RAG 意图召回：embedding 向量检索 Top-5 候选 ──
        if len(candidates) > 10:
            log.info(f"[L2 Classify] RAG starting: {len(candidates)} candidates")
            try:
                top5 = await asyncio.wait_for(
                    self._rag_recall_skills(message, candidates),
                    timeout=5.0,  # RAG 单独超时，不影响 LLM 分类
                )
                if top5 and len(top5) < len(candidates):
                    log.info(f"[L2 Classify] RAG recall: {len(candidates)}→{len(top5)} candidates")
                    candidates = top5
                else:
                    log.info(f"[L2 Classify] RAG returned {len(top5) if top5 else 0} candidates (no reduction)")
            except (asyncio.TimeoutError, Exception) as e:
                log.warning(f"[L2 Classify] RAG recall failed ({type(e).__name__}): {e}")

        async def _try_classify(model):
            return await llm_service.chat_sync(
                message=classify_prompt,
                system_prompt="意图分类器。返回JSON: {\"action\":\"操作名或NONE或UNSUPPORTED或CHAT\",\"confidence\":0.0~1.0}。操作类无工具→UNSUPPORTED。分析类无匹配→NONE。闲聊/寒暄/讨论→CHAT。",
                model_name=model,
            )

        try:
            # 先试会话模型，15s 超时；不行降级到 turbo
            from app.agents.settings.model import MODEL_CONFIG
            # L2 分类始终用决策模型，不受前端选择影响
            classify_model = MODEL_CONFIG.get("decision_model")
            result = await asyncio.wait_for(_try_classify(classify_model), timeout=30.0)
            result = (result or "").strip().strip('"').strip("'")
            # 尝试解析 JSON: {"action":"xxx","confidence":0.9}
            action_name = None
            confidence = 0.75
            try:
                data = _json_l2.loads(result)
                action_name = data.get("action", "")
                confidence = float(data.get("confidence", 0.75))
            except (_json_l2.JSONDecodeError, ValueError):
                action_name = result  # JSON解析失败，用原文作为action名
            if action_name == "UNSUPPORTED":
                return "UNSUPPORTED", "llm", 0.0
            if action_name == "CHAT":
                return "CHAT", "chat", confidence
            if action_name and action_name != "NONE":
                if action_name in known:
                    # 低置信不硬猜（业界标准）：confidence < 0.6 视为无匹配 → 走澄清/DynamicPlanner
                    if confidence < 0.6:
                        log.info(f"[L2 Classify] {action_name} 低置信({confidence}) → 不硬猜，走澄清/DynamicPlanner")
                        return None, "llm", confidence
                    log.info(f"[L2 Classify] {action_name} conf={confidence} ({len(candidates)} candidates)")
                    return action_name, "rag_llm" if rag_used else "llm", confidence
                for name in known:
                    if name in action_name or action_name in name:
                        log.info(f"[L2 Classify] fuzzy: {action_name} → {name}")
                        return name, "rag_llm" if rag_used else "llm", confidence
                # Token overlap match
                r_tokens = set(result.lower().split('_'))
                for name in known:
                    n_tokens = set(name.lower().split('_'))
                    common = r_tokens & n_tokens
                    if len(common) >= 2 or (len(common) == 1 and len(r_tokens - common) <= 1):
                        log.info(f"[L2 Classify] token fuzzy: {result} → {name} (common={common})")
                        return name, "rag_llm" if rag_used else "llm", 0.6
                log.warning(f"[L2 Classify] unknown action: {result}")
        except asyncio.TimeoutError:
            log.warning(f"[L2 Classify] timeout (8s) for {len(candidates)} candidates")
        except Exception as e:
            log.warning(f"[L2 Classify] failed: {e}")

        log.warning(f"[L2 Classify] no LLM match for '{message}'")
        return None, "llm", 0.0

    @staticmethod
    def _build_decision_pack(params: dict, context: dict, param_schema: list) -> dict:
        """构建审批决策包：风险等级 + 关联实体 + 规则检查。参数详情由前端参数列表渲染，此处不重复。"""
        # 风险等级
        is_delete = any("删除" in str(v) for v in (params or {}).values())
        risk_level = "high" if is_delete else ("medium" if len(params or {}) > 5 else "low")

        # 关联实体
        related = []
        for key, val in (context or {}).items():
            if isinstance(val, dict) and val.get("entity"):
                entity = val["entity"]
                label = val.get("label", key)
                name = entity.get("name") or entity.get("label") or str(entity.get("id", ""))
                related.append({"label": label, "value": name})

        # 规则检查：无规则时返回空，审批方不展示
        return {
            "risk_level": risk_level,
            "related_entities": related,
            "rule_checks": [],
        }

    async def _create_exception_ticket(
        self, conversation_id: str, user_id: str, message: str,
        error_type: str, error_detail: str, context: dict = None,
    ) -> str:
        """Agent 异常时创建工单到审批列表，人工介入处理。"""
        try:
            import json

            from app.db import get_db
            from app.models.message import ConfirmStatus, MessageRole, MessageType
            from app.repositories.message_repository import MessageRepository

            ticket_data = {
                "action_label": f"⚠️ 异常: {error_type}",
                "concept_label": "异常工作台",
                "tool": "exception_handling",
                "params": context or {},
                "param_schema": [],
                "risk": "exception",
                "user_id": user_id,
                "message": message[:120],
                "error_detail": error_detail[:500],
                "decision_pack": {
                    "risk_level": "high",
                    "related_entities": [],
                    "rule_checks": [],
                },
            }
            async for db_session in get_db():
                repo = MessageRepository(db_session)
                await repo.create(
                    conversation_id=conversation_id,
                    role=MessageRole.SYSTEM,
                    content=json.dumps(ticket_data, ensure_ascii=False),
                    message_type=MessageType.CONFIRM.value,
                    status=ConfirmStatus.PENDING.value,
                    assigned_to="系统管理员",
                )
                return "工单已创建，系统管理员将介入处理"
        except Exception as e:
            from app.core.logger import log
            log.error(f"[ExceptionTicket] 创建工单失败: {e}")
            return "系统异常，请稍后重试"

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
        import time as _t

        from app.core.tracing import span
        from app.services.llm_service import llm_service

        # 预加载 MCP 全局 overrides（跨 namespace 触发词）
        global _cached_mcp_overrides, _cached_mcp_ts
        try:
            _cached_mcp_overrides
        except NameError:
            _cached_mcp_overrides = {}
            _cached_mcp_ts = 0
        if _t.time() - _cached_mcp_ts > 60:
            try:
                from app.db import run_async as _ra2
                async def _load_mcp_ov():
                    from app.db import get_db
                    async for session in get_db():
                        from app.repositories.namespace_config_repo import NamespaceConfigRepository
                        repo = NamespaceConfigRepository(session)
                        return (await repo.get('_mcp', 'skill_overrides')) or {}
                    return {}
                _cached_mcp_overrides = _ra2(_load_mcp_ov()) or {}
                _cached_mcp_ts = _t.time()
            except Exception:
                _cached_mcp_overrides = {}

        # 埋点计时
        _t_start = _t.time()

        def _track(action, method, confidence, conv_id, msg, elapsed_ms=0, extra=None):
            """异步埋点，不阻塞主流程。"""
            try:
                from app.core.tracking import track_route
                track_route(
                    conversation_id=conv_id,
                    message=msg,
                    action_name=action,
                    method=method,
                    confidence=confidence,
                    elapsed_ms=elapsed_ms,
                    context=extra,
                )
            except Exception:
                pass

        # 多轮意图：上一条 Agent 含 ASK 标记 → 标记为追问上下文（不拦截，L2 优先）
        _is_ask_followup = False
        if history_messages:
            for hm in reversed(history_messages):
                role = getattr(hm, 'type', '') or getattr(hm, 'role', '')
                if role in ('ai', 'assistant', 'agent'):
                    last_agent = str(getattr(hm, 'content', ''))
                    _is_ask_followup = ('需要确认' in last_agent or '请确认' in last_agent
                                        or '哪方面' in last_agent or '具体指' in last_agent)
                    break

        # 是非问/追问：结合上一轮分析结论简短回答，不重查数据（治"过度发挥"）
        _yesno_ctx = ""
        if history_messages:
            for hm in reversed(history_messages):
                role = getattr(hm, 'type', '') or getattr(hm, 'role', '')
                if role in ('ai', 'assistant', 'agent'):
                    _yesno_ctx = str(getattr(hm, 'content', ''))[:2000]
                    break
        if (_yesno_ctx and len(message.strip()) < 15
                and _re.search(r'(吗|呢|？|\?|有没有|是不是|会不会|是否|能否|能不能)', message)
                and not _re.search(r'(查|看|找|列|统计|显示|获取|搜索|导出|生成|帮我|请)', message)):
            yield ('route_match', _json.dumps({"method": "yesno", "tool": "yesno", "confidence": 1.0, "concept_label": "追问回答"}))
            _track("yesno", "yesno", 1.0, session_id, message, elapsed_ms=int((_t.time() - _t_start) * 1000))
            from app.services.llm_service import llm_service
            _yn_prompt = f"基于上一轮分析结论，简短回答用户的是非追问（1-2 句话，直接给结论，不要展开、不要表格、不要重新查询数据）。\n\n上一轮结论：\n{_yesno_ctx}\n\n用户追问：{message}"
            async for _ct, _cc in llm_service.chat_stream(
                message=_yn_prompt, session_id=session_id, model_name=model_name,
                system_prompt="你是简洁的追问回答助手，只输出1-2句结论，不要展开、不要表格。",
                enable_thinking=False, history_messages=None, use_agent=False, web_search=False,
            ):
                yield (_ct, _cc)
            yield ('execution_done', _json.dumps({"method": "yesno", "totalSteps": 1}))
            return

        # 排序澄清：上一条是「排序依据不明确」反问，本消息是排序词（时间/数量/进度）→ 重新按排序词查询原对象
        _sort_reply = None
        if history_messages:
            for hm in reversed(history_messages):
                role = getattr(hm, 'type', '') or getattr(hm, 'role', '')
                if role in ('ai', 'assistant', 'agent'):
                    last_agent = str(getattr(hm, 'content', ''))
                    if '排序依据不明确' in last_agent or '按数量、进度还是时间' in last_agent:
                        _SORT_FIELD = {'时间': '__time__', '日期': '__time__', '开工日期': '__time__', '完工日期': '__time__',
                                       '数量': 'quantity', '生产数量': 'quantity', '进度': 'progress'}
                        _reply = message.strip()
                        if _reply in _SORT_FIELD:
                            _sort_field = _SORT_FIELD[_reply]
                            for hm2 in reversed(history_messages):
                                _r2 = getattr(hm2, 'type', '') or getattr(hm2, 'role', '')
                                if _r2 in ('user', 'human'):
                                    _orig = str(getattr(hm2, 'content', ''))
                                    if _orig and _orig != _reply:
                                        message = _orig
                                        _dir = 'ASC' if ('最小' in _orig or '最少' in _orig or '最早' in _orig or '最旧' in _orig) else 'DESC'
                                        _sort_reply = (_sort_field, _dir)
                                    break
                    break

        # 反馈闭环：用户说不是/不对/取消 → 记录为纠正信号
        _is_correction = _re.search(r'^不是|^不对|^取消|^搞错了|^我.*不是', message.strip())
        if _is_correction:
            log.info(f"[{self.name}] 用户纠正信号: {message[:50]}")

        # 判断消息是否为"片段回复"（需要从历史补全上下文），而非完整查询
        original_message = message
        _msg = message.strip()
        # 完整查询特征：含中文动词/查询意图关键词，不需要历史
        _complete_kw = ('查询', '列出', '显示', '查看', '获取', '搜索', '创建', '删除', '修改', '更新',
                        '列表', '全部', '所有', '统计', '分析', '报告', 'list', 'all')
        _is_complete = any(kw in _msg for kw in _complete_kw)
        # 短回复特征：纯编码/数字/简短确认，需要历史上下文
        _is_fragment = len(_msg) < 15 and not _is_complete
        if _is_fragment and history_messages:
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
        log.info(f"[{self.name}] onto_tools={len(onto_tools) if onto_tools else 0}")
        if onto_tools:
            try:
                from app.services.action_executor import action_executor
                from app.services.intent_router import intent_router

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
                    mcp_in_list = [c['name'] for c in candidate_list if c['name'].startswith('mcp_')]
                    if mcp_in_list:
                        log.info(f"[Trigger] candidate_list has MCP: {mcp_in_list}")
                    l2_name = None
                    l2_method = "llm"
                    l2_confidence = 0.0
                    rag_count = 0
                    if candidate_list:
                        # 1) 触发词匹配（用原始消息，不受历史拼接影响）
                        l2_name, l2_method, l2_confidence = self._trigger_match(original_message, candidate_list)
                        # 2) 未命中 → RAG 缩减 → LLM（同样用原始消息）
                        if not l2_name:
                            rag_count = len(candidate_list)
                            if len(candidate_list) > 10:
                                try:
                                    reduced = await asyncio.wait_for(
                                        self._rag_recall_skills(original_message, candidate_list),
                                        timeout=5.0,
                                    )
                                    if reduced and len(reduced) < len(candidate_list):
                                        candidate_list = reduced
                                except Exception:
                                    pass
                            async with span("route_intent", "generic"):
                                l2_name, l2_method, l2_confidence = await self._llm_classify_action(
                                    original_message, candidate_list, model_name,
                                    rag_used=(rag_count > 0 and rag_count > len(candidate_list)),
                                )
                    # 计算候选概念（中文）
                    concept_names = list(dict.fromkeys(
                        c["concept_label"] or c["concept_name"] for c in candidate_list if c.get("concept_name")
                    )) if candidate_list else []

                    if l2_name == 'CHAT':
                        # 闲聊/寒暄/讨论：直接自由对话，不套查询模板、不走工具路由
                        yield ('route_match', _json.dumps({
                            "method": "chat", "tool": "chat", "confidence": l2_confidence,
                            "concept_label": "自由对话",
                        }))
                        _track("CHAT", "chat", l2_confidence, session_id, original_message, elapsed_ms=int((_t.time() - _t_start) * 1000))
                        from app.core.prompts import DEFAULT_SYSTEM_PROMPT
                        async for _ct, _cc in llm_service.chat_stream(
                            message=original_message, session_id=session_id, model_name=model_name,
                            system_prompt=DEFAULT_SYSTEM_PROMPT, enable_thinking=enable_thinking,
                            history_messages=history_messages, use_agent=False, web_search=web_search,
                        ):
                            yield (_ct, _cc)
                        yield ('execution_done', _json.dumps({"method": "chat", "totalSteps": 1}))
                        return
                    if l2_name == 'UNSUPPORTED':
                        await self._create_exception_ticket(
                            conversation_id=session_id, user_id=user_id,
                            message=original_message, error_type="不支持的操作",
                            error_detail=f"用户请求: {original_message}，候选操作: {[c['name'] for c in candidate_list[:5]]}",
                        )
                        ops = [c['label'] for c in candidate_list if not c['name'].endswith('_query')]
                        if ops:
                            hint = f"支持的写操作：{'、'.join(ops[:5])}{'等' if len(ops) > 5 else ''}"
                        else:
                            hint = "当前仅支持查询与分析类操作"
                        yield ('content', f"抱歉，「{original_message}」操作暂未开放。{hint}。异常已记录，管理员将介入处理。")
                        yield ('done', _json.dumps({"unsupported": True}))
                        yield ('data_source', _json.dumps({"source": "none", "hint": "unsupported_action"}))
                        _track("UNSUPPORTED", "llm", l2_confidence, session_id, original_message, elapsed_ms=int((_t.time() - _t_start) * 1000))
                        return
                    elif l2_name:
                        yield ('route_l2', _json.dumps({
                            "candidateCount": len(candidate_list),
                            "ragCount": rag_count,
                            "concepts": concept_names,
                            # 候选 action 明细（name + 中文标签），供前端展示完整候选
                            "actions": [
                                {"name": c.get("name", ""), "label": c.get("label", ""), "concept_label": c.get("concept_label", "")}
                                for c in candidate_list if c.get("name")
                            ],
                            "ragUsed": rag_count > 0 and rag_count > len(candidate_list),
                        }))
                        _track(l2_name, l2_method, l2_confidence, session_id, original_message, elapsed_ms=int((_t.time() - _t_start) * 1000))
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
                                    _track("dynamic_plan", "dynamic", 0.5, session_id, original_message, elapsed_ms=int((_t.time() - _t_start) * 1000))
                                    return
                        except Exception as e:
                            log.warning(f"[{self.name}] 动态规划异常: {e}")

                    # L2 返回 NONE
                    if not l2_name:
                        # ASK 追问上下文 → 动态规划处理回复
                        if _is_ask_followup:
                            try:
                                from app.core.chain_engine import chain_engine as _ce3
                                if _ce3._get_compiled_runtime():
                                    log.info(f"[{self.name}] ASK追问→动态规划")
                                    async for evt_type, evt_data in _ce3._execute_dynamic(
                                        message=message, model_name=model_name,
                                        enable_thinking=enable_thinking, session_id=session_id,
                                        history_messages=history_messages,
                                    ):
                                        if evt_type == 'error':
                                            break
                                        yield (evt_type, evt_data)
                                    else:
                                        yield ('execution_done', _json.dumps({"method": "dynamic_plan"}))
                                        _track("dynamic_plan", "dynamic", 0.5, session_id, original_message, elapsed_ms=int((_t.time() - _t_start) * 1000))
                                        return
                            except Exception as e:
                                log.warning(f"[{self.name}] ASK追问→动态规划失败: {e}")

                        # 轻量追问
                        if candidate_list:
                            domains = list(dict.fromkeys(c.get("concept_label", "其他") for c in candidate_list[:10]))[:3]
                            domain_hint = "、".join(domains)
                            yield ('content', f"您想了解哪方面？比如：{domain_hint}等。请再描述一下具体需求。")
                            yield ('execution_done', _json.dumps({"method": "clarify"}))
                            _track("NONE", "llm", 0.0, session_id, original_message, elapsed_ms=int((_t.time() - _t_start) * 1000), extra={"reason": "clarify"})
                            return
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
                        "method": l2_method,
                        "tool": routing_result.tool_name,
                        "confidence": l2_confidence,
                        "concept_label": routing_result.concept_label,
                        "action_label": routing_result.action_label,
                    }))

                    if not routing_result.has_handler:
                        yield ('content', f"抱歉，「{original_message}」操作暂未开放。当前仅支持查询与分析类操作。")
                        yield ('done', _json.dumps({"unsupported": True}))
                        yield ('data_source', _json.dumps({"source": "none", "hint": "unsupported_action"}))
                        return

                    # ── Confirmation check ──
                    if routing_result.requires_confirmation:
                        # ── 确认路由：inline vs 委托审批 ──
                        from app.services.auth_service import auth_service as _auth_svc
                        user_roles = await _auth_svc.get_effective_roles(user_id) if user_id else set()
                        required_roles = set(routing_result.authorized_roles or [])
                        needs_delegation = required_roles and not (user_roles & required_roles)

                        # L1: 确定性正则提取（枚举/日期/数量）
                        prefill = intent_router.extract_params(original_message, routing_result.tool_name)
                        # L1.5 分层提取：编码/中文名称参数由 LLM 填槽，覆盖正则误提取
                        _llm_prefill = await intent_router.extract_params_llm(
                            original_message, routing_result.tool_name,
                        )
                        if _llm_prefill:
                            for _k, _v in _llm_prefill.items():
                                if _v:
                                    prefill[_k] = _v
                        # L2: resolve entity references (列表查询时跳过历史上下文)
                        prefill = await intent_router.resolve_entities(
                            original_message if _is_complete else message,
                            routing_result.tool_name, prefill,
                        )
                        # L3: fall back to LLM params for anything still empty
                        for k, v in (routing_result.params or {}).items():
                            if k not in prefill or not prefill.get(k):
                                prefill[k] = v
                        # L3.5: 用户指定了主键 → 查询现有数据预填表单
                        _sig = action_executor._sigs.get(routing_result.tool_name, {})
                        _concept_name = _sig.get("conceptName", "")
                        _concept = ontology_service.get_concept(_concept_name)
                        if _concept:
                            _pk = next((p["name"] for p in _concept.get("properties", []) if p.get("isPrimary")), None)
                            if _pk and prefill.get(_pk):
                                from app.services.data_backend import data_backend
                                _existing = await data_backend.resolve_entity(_concept_name, str(prefill[_pk]))
                                if _existing:
                                    # 只预填 action 参数定义的字段
                                    _param_names = {p["name"] for p in _sig.get("params", [])}
                                    for _k in _param_names:
                                        _v = _existing.get(_k)
                                        if _v is not None and _v != "":
                                            # datetime 截断到日期部分
                                            if isinstance(_v, str) and "T" in str(_v):
                                                _v = str(_v).split("T")[0]
                                            elif isinstance(_v, str) and " " in str(_v) and len(str(_v)) > 10:
                                                _v = str(_v).split(" ")[0]
                                            if _k not in prefill or not prefill.get(_k):
                                                prefill[_k] = _v
                        # L4: ontology graph traversal — enrich params + context
                        enriched = await intent_router.enrich_params(routing_result.tool_name, prefill)
                        param_schema = await intent_router.get_param_schema(routing_result.tool_name)

                        # 始终先走内联确认，用户确认后再分流
                        confirm_event = self._prepare_confirmation(session_id, message=original_message, action_label=routing_result.action_label)
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
                            _pack = self._build_decision_pack(confirmed_params or enriched.get('params', {}), enriched.get('context', {}), param_schema)
                            yield ('confirm_delegated', _json.dumps({
                                "tool": routing_result.tool_name,
                                "action_label": routing_result.action_label,
                                "concept_label": routing_result.concept_label,
                                "params": confirmed_params or enriched.get('params', {}),
                                "param_schema": param_schema,
                                "risk": "write",
                                "assigned_to": list(required_roles),
                                "context": enriched.get('context', {}),
                                "decision_pack": _pack,
                            }))
                            assigned_role = list(required_roles)[0]
                            yield ('content', f"已确认操作并提交 **{assigned_role}** 审批。审批进度可在「待审批」菜单查看。")
                            yield ('execution_done', _json.dumps({
                                "totalSteps": 4, "cancelled": True, "delegated": True,
                            }))
                            return

                        params = confirmed_params
                    else:
                        # L1: 确定性正则提取（枚举/日期/数量）
                        params = intent_router.extract_params(original_message, routing_result.tool_name)
                        # 数值聚合歧义（如"最大的工单"但对象有多个数值字段）→ 反问，不硬查
                        if params.get('_order_ambiguous'):
                            params.pop('_order_ambiguous', None)
                            if _sort_reply:
                                # 排序澄清回答（如「时间」）→ 用回答的排序字段 + 方向直接查询
                                params['_order_by'] = _sort_reply[0]
                                params['_order_dir'] = _sort_reply[1]
                                params['_limit'] = 1
                                log.info(f"[{self.name}] 排序澄清回答 → {_sort_reply[0]} {_sort_reply[1]}")
                            else:
                                _clarify_text = f"「{original_message}」里的排序依据不明确，请补充说明：按数量、进度还是时间？"
                                yield ('clarify_required', _json.dumps({"reason": "order_ambiguous", "question": _clarify_text}, ensure_ascii=False))
                                yield ('content', _clarify_text)
                                yield ('execution_done', _json.dumps({"method": "clarify", "reason": "order_ambiguous"}))
                                _track("clarify", "llm", 0.0, session_id, original_message, elapsed_ms=int((_t.time() - _t_start) * 1000), extra={"reason": "order_ambiguous"})
                                return
                        # L1.5 分层提取：编码/中文名称参数（格式不固定）由 LLM 填槽，覆盖正则误提取；
                        # 无编码/中文参数时 extract_params_llm 过滤后直接返回空、不产生 LLM 调用。
                        _llm_params = await intent_router.extract_params_llm(
                            original_message, routing_result.tool_name,
                        )
                        if _llm_params:
                            for _k, _v in _llm_params.items():
                                if _v:
                                    params[_k] = _v
                        # L2: resolve entity references (列表查询时跳过历史上下文)
                        _resolve_msg = original_message if _is_complete else message
                        params = await intent_router.resolve_entities(
                            _resolve_msg, routing_result.tool_name, params,
                        )
                        # L3: fall back to LLM params for anything still empty
                        # （排除 fuzzy 噪音：已由 L1.5 LLM 填槽产出结构化参数时，不再回填路由阶段的 _fuzzy）
                        for k, v in (routing_result.params or {}).items():
                            if k in ('_fuzzy', '_fuzzy_op'):
                                continue
                            if k not in params or not params.get(k):
                                params[k] = v
                        # 参数修正: 从消息提取编码, 优先填入主键
                        # 显式参数已填充时跳过 + 列表查询时跳过
                        if not _is_complete and not params:
                            import re as _re2
                            _m = _re2.search(r'[A-Z]{2,}[\d-]+', message)
                            if _m:
                                _cn = getattr(routing_result, 'concept_name', None) or routing_result.tool_name.replace("_query", "")
                                if _cn:
                                    _concept = ontology_service.get_concept(_cn)
                                    if _concept:
                                        for _prop in _concept.get("properties", []):
                                            if _prop.get("isPrimary"):
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
                    # MCP 工具：把原始消息作为参数传给 MCP Server
                    if routing_result.tool_name.startswith('mcp_'):
                        params['_message'] = original_message
                    async with span("tool_exec", "tool"):
                        tool_result = await action_executor.execute_structured_async(
                            routing_result.tool_name, params, user_id=user_id,
                        )
                    yield ('tool_result', _json.dumps({
                        "tool": routing_result.tool_name,
                        "label": sig.get("actionLabel", "") or sig.get("conceptLabel", ""),
                        "rowCount": tool_result.get("rowCount", 0),
                        "source": tool_result.get("source", ""),
                        "sourceLabel": tool_result.get("sourceLabel", ""),
                        "actionType": tool_result.get("actionType", "query"),
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
                                _schema = await intent_router.get_param_schema(routing_result.tool_name)
                                _pack = self._build_decision_pack(params, {}, _schema)
                                yield ('confirm_delegated', _json.dumps({
                                    "tool": routing_result.tool_name,
                                    "action_label": f"{routing_result.action_label}（规则审批: {rule_labels}）",
                                    "concept_label": routing_result.concept_label,
                                    "params": params,
                                    "param_schema": _schema,
                                    "risk": "write",
                                    "assigned_to": assigned,
                                    "context": {"rule_approval": rule_labels},
                                    "decision_pack": _pack,
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
                            "actionType": tool_result.get("actionType", "query"),
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
                            "actionType": tool_result.get("actionType", "query"),
                        }))

                    # ── LLM format only ──
                    yield ('format_start', _json.dumps({}))

                    from app.core.prompts import FORMAT_ONLY_SYSTEM_PROMPT, TABLE_COLUMN_RULE
                    tool_result_text = tool_result.get("result", "")

                    # 根据操作类型生成不同的格式化指令
                    _action_type = tool_result.get("actionType", "query")
                    if _action_type == "delete":
                        format_message = (
                            f"### 操作结果\n{tool_result_text}\n\n"
                            f"### 用户消息\n{message}\n\n"
                            f"请直接复述以上操作结果，不要添加表格或额外解释。一句话确认即可。"
                        )
                    else:
                        format_message = (
                            f"### 操作结果\n{tool_result_text}\n\n"
                            f"### 用户消息\n{message}\n\n"
                            f"请基于以上结果回复用户消息。{TABLE_COLUMN_RULE}。"
                        )
                        format_message = (
                            f"### 查询结果\n{tool_result_text}\n\n"
                            f"### 用户消息\n{message}\n\n"
                            f"请基于以上查询结果回复用户消息。{TABLE_COLUMN_RULE}。"
                        )

                    system_prompt = await self.build_system_prompt(include_tools_prompt=False, user_message=message)
                    system_prompt = f"{FORMAT_ONLY_SYSTEM_PROMPT}\n\n{system_prompt}"

                    # 格式化回复用决策模型（快速），不用前端大模型
                    from app.agents.settings.model import MODEL_CONFIG
                    async with span("format", "generic"):
                        async for t, c in llm_service.chat_stream(
                            message=format_message, session_id=session_id,
                            system_prompt=system_prompt,
                            model_name=MODEL_CONFIG.get("decision_model"),
                            use_agent=False, web_search=False,
                            history_messages=history_messages,
                            enable_thinking=False,
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
                await self._create_exception_ticket(
                    conversation_id=session_id, user_id=user_id,
                    message=original_message, error_type="系统异常",
                    error_detail=f"路由异常: {str(e)[:300]}",
                )
                yield ('content', "处理请求时发生错误，异常已记录，管理员将介入处理。")
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

        from app.services.auth_service import auth_service as _auth_svc
        from app.services.ontology_service import ontology_service

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
                yield ('content', "业务系统接口异常，自动切换至图数据库查询")
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
        from app.agents.settings.model import MODEL_CONFIG
        cypher_model = model_name or MODEL_CONFIG.get("decision_model")

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

        from app.core.prompts import CYPHER_ANALYSIS_SYSTEM_PROMPT, TABLE_COLUMN_RULE
        system_prompt = await self.build_system_prompt(include_tools_prompt=False, user_message=message)
        analysis_system = f"{CYPHER_ANALYSIS_SYSTEM_PROMPT}\n\n{system_prompt}"

        # 字段名映射：数据源字段 → 本体中文标签
        if records and concept_names:
            from app.services.ontology_service import ontology_service
            for cname in concept_names:
                concept = ontology_service.get_concept(cname)
                if concept:
                    prop_map = {p["name"]: p.get("label", p["name"]) for p in concept.get("properties", [])}
                    records = [{prop_map.get(k, k): v for k, v in r.items()} for r in records]
                    break

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
            f"1. {TABLE_COLUMN_RULE}，列顺序与 JSON 一致\n"
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
            from app.services.action_executor import action_executor
            from app.services.intent_router import intent_router
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
                tool_name, _, _ = await self._llm_classify_action(message, candidate_list, None)

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
        """规则自检响应质量（不消耗 LLM token）。
        返回 None 表示通过，返回字符串为修正后的响应。

        检查项：
          1. 空响应 / 过短 → 返回友好提示
          2. 无查询结果时 LLM 是否已说明
          3. 含查询结果表格时格式是否完整
        """
        if not response or len(response.strip()) < 5:
            return "抱歉，暂未获取到相关数据。请确认查询条件后重试。"

        text = response.strip()

        # 未找到记录的提示格式检查
        if "未找到" in text and "记录" in text:
            return None  # 已正常提示

        # 表格格式完整性检查：有表头行但无分隔行
        has_header = "|" in text and any(line.strip().startswith("|") for line in text.split("\n"))
        has_separator = "---" in text
        if has_header and not has_separator:
            # 表格格式不完整，补分隔行（简单修复）
            lines = text.split("\n")
            fixed = []
            for i, line in enumerate(lines):
                fixed.append(line)
                if line.strip().startswith("|") and not line.strip().startswith("|---"):
                    # 表头后的第一行如果是数据（非分隔符），插入分隔行
                    next_idx = i + 1
                    if next_idx < len(lines) and lines[next_idx].strip().startswith("|") and "---" not in lines[next_idx]:
                        cols = line.count("|") - 1
                        fixed.append("|" + "|".join(["---" for _ in range(max(cols, 1))]) + "|")
            return "\n".join(fixed)

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
        user_message: str = "",
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
            # Inject 领域通用知识（按用户问题向量检索，非静态映射）
            from app.core.tracing import span
            async with span("knowledge_retrieve", "io") as _ks:
                _knowledge = ontology_service.retrieve_domain_knowledge(user_message)
                if _ks is not None and _knowledge:
                    _ks["meta"].update({
                        "hit_count": len(_knowledge),
                        "hits": [(k.strip().split("\n")[0] or "未命名知识")[:40] for k in _knowledge],
                    })
                for _text in _knowledge:
                    prompt += f"\n\n{_text}"
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
