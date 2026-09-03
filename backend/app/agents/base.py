"""Agent 抽象基类"""
import asyncio
import re
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


# 影响分析类多跳问题的确定性识别（通用，不写死概念名）：
# 「XX 取消/延期/变更后影响哪些物料/库存」需沿概念关系图跨概念追查
# （订单→分录→物料→BOM→子件→库存/采购），单概念工具直查无法回答，
# 命中后短路到动态规划（含 BOM 展开规则 9）。
_IMPACT_ANALYSIS_RE = re.compile(
    r'|'.join([
        r'影响\s*[哪那]?(些|个|几|多少|什么|哪种|哪些)',
        r'[哪那]?(些|个|几|多少|什么|哪些)[^。？?！]{0,6}(被)?\s*影响',
        r'(取消|延期|变更|停用|废止|减少|中断|调整|推后|提前)[^。？?！]{0,10}影响',
        r'影响[^。？?！]{0,12}(库存|物料|生产|采购|成本|交期|到货|订单|工单|供应|排产)',
        r'(BOM|耗用|用量|用多少)[^。？?！]{0,8}影响',
        r'波及|连锁反应|连带影响',
    ])
)
# 无「影响」字但同属跨概念分析的词（多采购/超交/发多/风险检测），同样短路到动态规划
_OVERBUY_ANALYSIS_RE = re.compile(
    r'|'.join([
        r'采购(过|超)(量|买)|过量采购|超量采购|多采购|买多|买超',
        r'(Daily\s*Schedule|Schedule|交付|发单|发料).{0,6}(发多|多发|超交|超量)',
        r'(发多|多发|超交)(了|的|吗|没有|多少)',
        r'物料.{0,6}(积压|呆滞|呆料|风险)',
    ])
)


def _is_impact_analysis(message: str) -> bool:
    """识别「影响分析」类多跳问题：优先用 FC 链配置（agent_chains 表的 triggers 正则，
    业务可配置、不写死），链未加载/未命中时回退到内置正则兜底。"""
    m = (message or "").strip()
    if not m:
        return False
    # 1. 链配置优先（triggers 是声明式正则，前端「链条配置」可改）
    try:
        from app.core.chain_engine import _CHAINS, reload_chains
        if not _CHAINS:
            reload_chains()
        _ml = m.lower()
        for _cid, _cfg in _CHAINS.items():
            for _pat in _cfg.get("triggers", []):
                try:
                    if re.search(_pat, _ml):
                        return True
                except re.error:
                    continue
    except Exception:
        pass
    # 2. 内置正则兜底（链未配置时仍能识别常见影响分析问法）
    if '影响' in m:
        return bool(_IMPACT_ANALYSIS_RE.search(m))
    return bool(_OVERBUY_ANALYSIS_RE.search(m))


def _chat_capability_section() -> str:
    """CHAT 自由对话的能力清单 — 让「你有什么功能/能做什么」基于真实业务域能力回答。

    数据源：action_executor._sigs（本体 action 签名）+ _concepts（概念中文标签）。
    生成失败静默返回空串（闲聊路径不因清单失败而中断）。
    """
    try:
        from app.services.ontology_service import ontology_service
        from app.services.action_executor import action_executor

        meta = ontology_service.meta or {}
        ns = meta.get("namespace") or meta.get("projectName") or ""
        action_executor._ensure_loaded()

        concepts = getattr(action_executor, "_concepts", {}) or {}
        # 概念 → 有序 action 清单（排除 MCP 回环工具）
        by_concept: dict = {}
        for _fn, sig in (action_executor._sigs or {}).items():
            if sig.get("source") == "mcp":
                continue
            cname = sig.get("conceptName") or ""
            if not cname:
                continue
            aname = sig.get("actionName") or (
                _fn[len(cname) + 1:] if _fn.startswith(cname + "_") else _fn
            )
            action_def = {}
            for a in (concepts.get(cname, {}).get("actions") or []):
                if a.get("name") == aname:
                    action_def = a
                    break
            label = action_def.get("label") or aname
            entry = f"{label}（需确认）" if sig.get("requiresConfirmation") else label
            slot = by_concept.setdefault(
                cname, {"label": concepts.get(cname, {}).get("label") or cname, "actions": []})
            if entry not in slot["actions"]:
                slot["actions"].append(entry)

        if not by_concept:
            return ""

        lines = [f"- {v['label']}：{'、'.join(v['actions'])}" for v in by_concept.values()]
        header = (f"当前激活业务本体：{ns}（{len(by_concept)} 类业务对象）"
                  if ns else f"共 {len(by_concept)} 类业务对象")
        return (
            f"\n\n## 当前业务域能力（真实清单，动态生成）\n{header}\n"
            "用户问「你有什么功能 / 能做什么 / 有哪些操作」时，必须基于以下清单回答，"
            "并用示例问法引导（如「查一下38开头的工单」「创建一张工单」）；"
            "**禁止声称不具备的能力**（如网络搜索、企业信息查询、发送邮件等）：\n"
            + "\n".join(lines)
        )
    except Exception:
        return ""


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

    # 指代词集合：这些词出现说明消息是指代延续，需要消解而非当搜索值
    _COREF_WORDS = ('其它', '其他', '别的', '还有', '另外', '第二个', '下一个', '这个', '那个', '这些', '那些', '它', '该', '其余', '剩下的')

    async def _resolve_coreference(self, message: str, history_messages: Optional[List]) -> Optional[str]:
        """通用指代消解：把含指代词的消息 + 上文历史，消解成明确的查询意图。

        多轮对话中「其它产线的」「第二个呢」这类省略指代，光靠各层正则无法可靠理解。
        这里用一次 LLM 调用，结合上文历史（用户原话 + assistant 的结构化身份：
        agent/tool/params），产出消解后的明确意图，供后续分类/参数提取/填槽统一使用。

        返回消解后的明确消息；非指代消息或消解失败返回 None（走原流程）。
        """
        _msg = message.strip()
        if not _msg or len(_msg) > 50:
            return None
        # 快速判定：含指代词才算指代延续（避免每条短消息都多一次 LLM 调用）
        if not any(w in _msg for w in self._COREF_WORDS):
            return None

        from app.services.history_projection import TURN_META_KEY

        # 组装上文历史（用户原话 + assistant 结构化身份，取代文本标签拼接）
        hist_parts = []
        for hm in (history_messages or [])[-6:]:
            role = getattr(hm, 'type', '') or getattr(hm, 'role', '')
            content = str(getattr(hm, 'content', ''))[:200]
            if role in ('user', 'human'):
                hist_parts.append(f"用户：{content}")
            elif role in ('ai', 'assistant', 'agent'):
                turn_meta = getattr(hm, 'additional_kwargs', {}).get(TURN_META_KEY) if hasattr(hm, 'additional_kwargs') else None
                if isinstance(turn_meta, dict):
                    label = turn_meta.get('agent_label') or turn_meta.get('agent_name') or '助手'
                    tool = turn_meta.get('tool') or ''
                    params = turn_meta.get('params') or {}
                    if tool:
                        hist_parts.append(f"助手[{label}] 用工具 {tool} 查询，参数 {params if params else '无'}")
                    else:
                        hist_parts.append(f"助手[{label}]：{content[:80]}")
                else:
                    hist_parts.append(f"助手：{content}")
        hist_text = "\n".join(hist_parts) if hist_parts else "(无历史)"

        prompt = (
            "用户在多轮对话中说了含指代词的一句话，需要你结合上文把它消解成明确的查询意图。\n\n"
            f"上文历史：\n{hist_text}\n\n"
            f"用户当前消息：{message}\n\n"
            "规则：\n"
            "- 指代词（其它/别的/还有/第二个/这个等）指代的是上文已出现的对象或能力\n"
            "- 输出消解后的完整明确意图，补全省略的主语、宾语、查询对象\n"
            "- 例如「你试一下其它产线的」→「查询除上文产线 P001 之外的所有产线的能耗」\n"
            "- 例如「第二个呢」→「查询上文列表中的第二个对象」\n"
            "- 不要编造上文不存在的对象；消解不了就原样返回用户消息\n"
            "只输出消解后的句子，不要解释。"
        )
        try:
            from app.agents.settings.model import MODEL_CONFIG
            from app.services.llm_service import llm_service
            result = await asyncio.wait_for(
                llm_service.chat_sync(
                    message=prompt,
                    system_prompt="你是指代消解器，把多轮对话中的省略指代补全为明确查询意图，只输出一句话。",
                    model_name=MODEL_CONFIG.get("decision_model"),
                ),
                timeout=8.0,
            )
            result = (result or "").strip()
            if result and result != message:
                log.info(f"[Coref] 指代消解: '{message[:30]}' → '{result[:60]}'")
                return result
        except Exception as e:
            log.warning(f"[Coref] 指代消解失败，走原流程: {e}")
        return None

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
    # 写操作澄清挂起状态（轮内挂起，对齐 DSH ask_user_question）：session_id → {event, reply}
    _pending_clarify: dict = {}

    @classmethod
    def resolve_confirmation(cls, session_id: str, approved: bool, params: dict = None):
        """Called by API endpoint to resolve a pending confirmation."""
        log.debug(f"[Confirm] resolve_confirmation called: session_id={session_id}, approved={approved}")
        entry = cls._pending_confirmations.get(session_id)
        if entry:
            entry["approved"] = approved
            # body 未带 params 时回退挂起时的原参数（确认=批准原方案，不应变成空参数）
            entry["params"] = params or entry.get("params") or {}
            entry["event"].set()
            log.debug(f"[Confirm] resolve_confirmation SUCCESS: session_id={session_id}")
            return True
        log.warning(f"[Confirm] resolve_confirmation FAILED: session_id={session_id} not found in pending")
        return False

    @classmethod
    def resolve_clarify(cls, session_id: str, reply: str = "", cancelled: bool = False,
                        selected: list = None, custom: str = ""):
        """Called by API endpoint to resolve a pending clarification（用户补充参数或取消）。

        selected：用户点选的候选（label 列表，确定性值，不再经 LLM 文本提取）。
        custom：用户自由输入文本（区别于 selected）。
        reply：旧字段自由文本（兼容，未传 selected/custom 时回退用它）。
        """
        entry = cls._pending_clarify.get(session_id)
        if entry:
            entry["reply"] = reply or ""
            entry["selected"] = selected or []
            entry["custom"] = custom or ""
            entry["cancelled"] = bool(cancelled)
            entry["event"].set()
            log.debug(f"[Clarify] resolve_clarify SUCCESS: session_id={session_id} reply={reply[:40]!r} selected={selected}")
            return True
        log.warning(f"[Clarify] resolve_clarify FAILED: session_id={session_id} not found in pending")
        return False

    def _prepare_clarify(self, session_id: str) -> asyncio.Event:
        """Register a pending clarification BEFORE yielding clarify_required."""
        event = asyncio.Event()
        self._pending_clarify[session_id] = {"event": event, "reply": "", "cancelled": False}
        log.debug(f"[Clarify] prepare: session_id={session_id}")
        return event

    async def _wait_for_clarify(self, session_id: str, event: asyncio.Event, timeout: float = 300.0) -> tuple:
        """Wait for frontend to answer a clarification. Returns (cancelled, reply, selected, custom)."""
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            log.warning(f"[Clarify] session {session_id} 澄清超时 ({timeout}s)")
            self._pending_clarify.pop(session_id, None)
            return True, "", [], ""
        entry = self._pending_clarify.pop(session_id, None) or {}
        return (entry.get("cancelled", False), entry.get("reply", ""),
                entry.get("selected", []) or [], entry.get("custom", "") or "")

    def _prepare_confirmation(self, session_id: str, message: str = "", action_label: str = "", params: dict = None) -> asyncio.Event:
        """Register a pending confirmation BEFORE yielding confirm_required."""
        event = asyncio.Event()
        entry = {"event": event, "approved": False, "params": params or {}, "message": message, "action_label": action_label}
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
        rag_used: bool = False, history_turns: Optional[List[Dict]] = None,
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

        # 结构化上文（每轮由哪个 Agent/工具处理了什么），让分类器判断延续而非靠文本标签
        history_part = ""
        if history_turns:
            lines = []
            for t in history_turns:
                label = t.get("agent_label") or t.get("agent_name") or "?"
                tool = t.get("tool") or ""
                params = t.get("params") or {}
                if tool:
                    lines.append(f"- [{label}] 工具 {tool}，参数 {params if params else '无'}")
                elif label:
                    lines.append(f"- [{label}] 处理")
            if lines:
                history_part = "上文最近几轮（哪个 Agent/工具处理了什么）：\n" + "\n".join(lines) + "\n\n"

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
            "   例外：含[影响/后果/关联/依赖/会怎样/方案/怎么改/如何改/如何调整/怎样调整/建议]等分析或方案词 → 跳过 UNSUPPORTED，返回 NONE（让系统走多跳分析/生成变更方案）\n"
            "7. 仅当用户使用的动词与操作名确切匹配时才选中\n"
            "   如：创建→create, 删除→delete, 查询→query\n"
            "   复制/参考/照搬/仿照/按XX的样子/照着XX → 理解为「创建同类新对象（以XX的值作参考）」，匹配 create；XX 是源对象的编码/名称，交给后续参数提取\n"
            "8. 闲聊/寒暄/讨论类输入（问候、感谢、征求意见、纯知识讨论，无明确数据查询或操作意图）→ 返回 CHAT\n"
            "9. 「概念+字段」表述（如「工单状态」「工单数量」「设备状态」）中，字段是概念的属性，应匹配到该概念（「工单状态」→查询工单），不要当成独立查询对象\n"
            "10. 「最小/最大/最新/最早/最少/最多」是排序/聚合修饰词，不影响概念匹配（「最小的工单」→工单），修饰词交给后续参数提取处理\n"
            "11. 指代延续：用户消息含「其它/别的/还有/第二个/这个/那个」等指代词时，结合上文判断：\n"
            "    - 若上文由候选列表之外的某个工具/外部能力处理（如上文是能耗等外部数据），候选列表里没有对应工具 → 返回 NONE（让系统延续上文能力处理）\n"
            "    - 若上文对象仍在候选列表内 → 匹配该对象对应的工具\n"
            "返回JSON格式：{\"action\":\"操作名或NONE或UNSUPPORTED或CHAT\",\"confidence\":0.0~1.0}\n\n"
            f"可选操作（按概念域分组）：\n{options}\n\n"
            f"{history_part}"
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
            # L2 分类跟随对话模型（用户切换的统一模型），decision_model 仅兜底
            classify_model = model_name or MODEL_CONFIG.get("decision_model")
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
    def _build_decision_pack(params: dict, context: dict, param_schema: list, irreversible: bool = False) -> dict:
        """构建审批决策包：风险等级 + 关联实体 + 规则检查。参数详情由前端参数列表渲染，此处不重复。"""
        # 风险等级：删除=high；不可自动撤销的写操作至少 medium（不可回滚需谨慎审批）
        is_delete = any("删除" in str(v) for v in (params or {}).values())
        if is_delete:
            risk_level = "high"
        elif irreversible or len(params or {}) > 5:
            risk_level = "medium"
        else:
            risk_level = "low"

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
        log.info(f"[_standard_process] 进入 agent={self.name} message={message[:40]!r}")
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

        # ── 确定性影响分析短路（通用，不写死概念名）──
        # 「XX取消/延期/变更后影响哪些物料/库存」是明确的跨概念业务查询，
        # 必须优先于"是非追问"等启发式（短消息+问号易被误判为追问），
        # 直接走动态规划（含 BOM 展开 + 影响链路扩展）。
        if _is_impact_analysis(message):
            # 阶段 C Graph-Loop 融合：影响分析显式化为 LoopPlan graph 决定（执行层 Graph 引擎确定性扩散），留痕可观测
            from app.agents.loop import LoopPlan, LoopTracer
            _g_plan = LoopPlan(kind="graph", reason="影响分析→Graph 确定性扩散")
            _g_tracer = LoopTracer()
            _g_tracer.record(1, _g_plan.kind, _g_plan.reason, 0.0)
            log.info(f"[{self.name}] LoopPlan kind=graph reason={_g_plan.reason!r}")
            try:
                from app.core.chain_engine import chain_engine as _ce_impact
                if _ce_impact._get_compiled_runtime():
                    log.info(f"[{self.name}] 影响分析 → 动态规划（BOM展开/影响链路）")
                    _track("impact_analysis", "dynamic", 1.0, session_id, message,
                           elapsed_ms=int((_t.time() - _t_start) * 1000))
                    _ok = False
                    async for evt_type, evt_data in _ce_impact._execute_dynamic(
                        message=message, model_name=model_name,
                        enable_thinking=enable_thinking, session_id=session_id,
                        history_messages=history_messages,
                    ):
                        if evt_type == 'error':
                            log.warning(f"[{self.name}] 影响分析动态规划失败: {evt_data}")
                            break
                        yield (evt_type, evt_data)
                    else:
                        _ok = True
                    if _ok:
                        yield ('execution_done', _json.dumps({"method": "dynamic_plan"}))
                        return
            except Exception as _e:
                log.warning(f"[{self.name}] 影响分析动态规划异常: {_e}")
            # 动态规划失败/不可用 → 继续走正常流程兜底（不 return）

        # ── 协作意图显式化（阶段 E：多业务域协作 → LoopPlan collab 决定，留痕可观测）──
        try:
            from app.agents.collab import is_collab_intent, collab_reason
            if is_collab_intent(message):
                from app.agents.loop import LoopPlan, LoopTracer
                _c_plan = LoopPlan(kind="collab", reason=collab_reason(message))
                _c_tracer = LoopTracer()
                _c_tracer.record(1, _c_plan.kind, _c_plan.reason, 0.0)
                log.info(f"[{self.name}] LoopPlan kind=collab reason={_c_plan.reason!r}")
        except Exception:
            pass

        # 是非问/追问：结合上一轮分析结论简短回答，不重查数据（治"过度发挥"）
        _yesno_ctx = ""
        if history_messages:
            for hm in reversed(history_messages):
                role = getattr(hm, 'type', '') or getattr(hm, 'role', '')
                if role in ('ai', 'assistant', 'agent'):
                    _yesno_ctx = str(getattr(hm, 'content', ''))[:2000]
                    break
        # 启发式初筛（宽进）：短消息 + 疑问词 + 有上一轮结论；再用 LLM 精判是追问还是新查询
        if (_yesno_ctx and len(message.strip()) < 20
                and _re.search(r'(吗|呢|？|\?|有没有|是不是|会不会|是否|能否|能不能)', message)):
            from app.agents.settings.model import MODEL_CONFIG
            from app.services.llm_service import llm_service
            _judge_prompt = (
                f"判断用户消息是「对上一轮回答的简短追问」还是「新的数据查询」。\n"
                f"上一轮回答（节选）：\n{_yesno_ctx[:500]}\n\n"
                f"用户消息：{message}\n\n"
                f"规则：针对上一轮结论的简短追问（如「有方案吗」「会不会影响」）输出 true；"
                f"新的数据查询（如「有没有过期工单」「帮我查库存」）输出 false。只输出 true 或 false。"
            )
            try:
                _judge = await asyncio.wait_for(
                    llm_service.chat_sync(
                        message=_judge_prompt,
                        system_prompt="你是追问判别器，只输出 true 或 false。",
                        model_name=MODEL_CONFIG.get("decision_model"),
                    ),
                    timeout=5.0,
                )
                _is_yesno = 'true' in str(_judge).strip().lower()
            except Exception:
                _is_yesno = False
            if _is_yesno:
                yield ('route_match', _json.dumps({"method": "yesno", "tool": "yesno", "confidence": 1.0, "concept_label": "追问回答"}))
                _track("yesno", "yesno", 1.0, session_id, message, elapsed_ms=int((_t.time() - _t_start) * 1000))
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

        # ── 通用指代消解（多轮上下文延续）──
        # 含指代词的消息（其它/别的/还有/第二个等），用 LLM 结合上文历史消解为明确意图，
        # 供后续 L2 分类、参数提取、填槽统一使用，避免各层各自误判指代。
        _resolved = await self._resolve_coreference(original_message, history_messages)
        if _resolved:
            message = _resolved

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

                # ── 复合任务：LLM 规划判断（异常降级到动作词计数兜底）→ 走动态规划多步 ──
                if await self._is_compound_intent(original_message, model_name):
                    try:
                        from app.core.chain_engine import chain_engine as _ce3
                        if _ce3._get_compiled_runtime():
                            log.info(f"[{self.name}] 复合任务（LLM 判断）→ 动态规划")
                            async for _evt_type, _evt_data in _ce3._execute_dynamic(
                                message=message, model_name=model_name,
                                enable_thinking=enable_thinking, session_id=session_id,
                            ):
                                if _evt_type == 'error':
                                    log.warning(f"[{self.name}] 动态规划失败: {_evt_data}")
                                    break
                                yield (_evt_type, _evt_data)
                            return
                    except Exception as _ce_e:
                        log.warning(f"[{self.name}] 复合任务动态规划异常: {_ce_e}")

                if intent_router.ready:
                    # L2 静默分类（不先发事件，等确定模式后再发）
                    candidates = intent_router.get_candidates(self.name)
                    candidate_list = [
                        {"name": fn, "label": e.action_label, "description": e.description,
                         "concept_label": e.concept_label, "concept_name": e.concept_name}
                        for fn, e in candidates.items()
                    ]
                    # 业务域只是显示、执行统一：候选并入所有 *_query / 写工具，
                    # 让 L2 分类能命中任意业务域的查询工具（如「380000呢」→ ProcessRouting_query），
                    # 避免路由到 analysis_monitor 时因无查询工具而走 chat 凭空臆断。
                    try:
                        from app.services.action_executor import action_executor as _ae_cand
                        _ae_cand._ensure_loaded()
                        _seen_cand0 = {c["name"] for c in candidate_list}
                        for _fn, _sig in _ae_cand._sigs.items():
                            _an = (_sig.get("actionName") or "").lower()
                            _is_q = _fn.endswith("_query") or _an == "query" or _sig.get("outputType") in ("list", "query")
                            _is_w = (_an in ("create", "update", "delete", "schedule", "insertorder", "copy", "release", "close")
                                     or _fn.endswith(("_create", "_update", "_delete", "_schedule", "_insertOrder", "_copy")))
                            if (_is_q or _is_w) and _fn not in _seen_cand0:
                                candidate_list.append({
                                    "name": _fn,
                                    "label": _sig.get("actionLabel") or _sig.get("conceptLabel") or _fn,
                                    "description": _sig.get("description", "") or "",
                                    "concept_label": _sig.get("conceptLabel", ""),
                                    "concept_name": _sig.get("conceptName", ""),
                                })
                    except Exception:
                        pass
                    # 决策候选 = 当前 Agent 工具 + 所有 *_query 查询工具 + 所有写工具（跨 Agent）。
                    # 对齐 DSH react 循环：LLM 决策时若实体模糊（「物料38开头」），能先调查询工具
                    # （Material_query 等）看清候选，再据此创建；延续上文「创建工单」时能直接选
                    # WorkOrder_create（写工具有确认门禁，执行前仍会人机确认，风险可控）。
                    try:
                        from app.services.action_executor import action_executor as _ae_decide_cand
                        _ae_decide_cand._ensure_loaded()
                        _seen_cand = {c["name"] for c in candidate_list}
                        decision_candidates = list(candidate_list)
                        for _fn, _sig in _ae_decide_cand._sigs.items():
                            _an = (_sig.get("actionName") or "").lower()
                            _is_query = _fn.endswith("_query") or _an == "query" or _sig.get("outputType") in ("list", "query")
                            _is_write = (
                                _an in ("create", "update", "delete", "schedule", "insertorder", "copy", "release", "close")
                                or _fn.endswith(("_create", "_update", "_delete", "_schedule", "_insertOrder", "_copy"))
                            )
                            if not (_is_query or _is_write):
                                continue
                            if _fn in _seen_cand:
                                continue
                            decision_candidates.append({
                                "name": _fn,
                                "label": _sig.get("actionLabel") or _sig.get("conceptLabel") or _fn,
                                "description": _sig.get("description", "") or "",
                                "concept_label": _sig.get("conceptLabel", ""),
                                "concept_name": _sig.get("conceptName", ""),
                            })
                    except Exception as _e_cc:
                        log.warning(f"[{self.name}] 构建决策候选失败，回退 Agent 工具: {_e_cc}")
                        decision_candidates = candidate_list
                    mcp_in_list = [c['name'] for c in candidate_list if c['name'].startswith('mcp_')]
                    if mcp_in_list:
                        log.info(f"[Trigger] candidate_list has MCP: {mcp_in_list}")
                    l2_name = None
                    l2_method = "trigger"
                    l2_confidence = 0.0
                    rag_count = 0
                    if candidate_list:
                        # 只用触发词确定性匹配（快）。不再调 L2 的 LLM 分类：
                        # FC 决策循环（function calling）已含全量 query+write 工具、能自己选工具，
                        # L2 的 JSON 分类是冗余前置 LLM 调用（原 qwen3.6-plus 深度推理约 10s），
                        # 去掉后未命中由 FC 决策循环兜底选工具（对齐 DSH 单次 LLM 决策）。
                        l2_name, l2_method, l2_confidence = self._trigger_match(original_message, candidate_list)
                    # 计算候选概念（中文）
                    concept_names = list(dict.fromkeys(
                        c["concept_label"] or c["concept_name"] for c in candidate_list if c.get("concept_name")
                    )) if candidate_list else []

                    # ── 执行分发：L2 已产出决定，按 kind 分发到既有执行段（执行归确定性）──
                    # 说明：单步工具路径是「L2 一次分类 → 确定性执行一条龙」，无需 ReAct 逐轮循环；
                    # 多步/复合任务的 ReAct 循环已在 dynamic.py 的 DynamicPlanner.execute() 实现
                    # （先计划后执行的 Plan-then-Execute，业界标准），由 _execute_multi_step 接入。
                    from app.agents.planner import DefaultPlanner
                    from app.agents.loop import LoopTracer
                    _loop_plan = DefaultPlanner._to_plan(l2_name)
                    _loop_tracer = LoopTracer()
                    _loop_tracer.record(1, _loop_plan.kind, _loop_plan.reason or l2_name or "无匹配", 0.0)
                    log.info(f"[{self.name}] LoopPlan kind={_loop_plan.kind} reason={_loop_plan.reason!r}")

                    if _loop_plan.kind == 'chat':
                        async for _evt in self._execute_chat(
                            l2_confidence=l2_confidence, session_id=session_id,
                            original_message=original_message, model_name=model_name,
                            enable_thinking=enable_thinking, history_messages=history_messages,
                            web_search=web_search, _track=_track, _t_start=_t_start,
                        ):
                            yield _evt
                        return
                    if _loop_plan.kind == 'ask':
                        async for _evt in self._execute_ask(
                            original_message=original_message, session_id=session_id,
                            user_id=user_id, model_name=model_name,
                            l2_confidence=l2_confidence, candidate_list=candidate_list,
                            _track=_track, _t_start=_t_start,
                        ):
                            yield _evt
                        return
                    if _loop_plan.kind == 'tool':
                        # ── 统一循环（DSH 式 LLM 每轮决策）：决策 → 执行 → 结果喂回 → 再决策 ──
                        # 每轮 LLM 看观察（消息+候选工具+已执行结果）决定 tool/ask/done，
                        # 直到 done 或防死循环上限；单步多步统一，不预判跑几圈。
                        from app.agents.settings.model import MODEL_CONFIG as _MC
                        _max_rounds = 6
                        _results = []          # 已执行工具结果（喂回决策）
                        _final_text = ""        # done 时的最终结论
                        _final_routing = None
                        _final_tool_result = None
                        _cancelled = False
                        _last_query_tool = None  # 上一次执行的查询工具名，防「查到结果又重复查」死循环
                        # 对话上文：让 LLM 识别「选定物料/确认」这类延续意图，继续上文未完成的任务
                        # （如用户先「创建工单」→澄清→「用380000」，下一步应继续创建而非只查物料）。
                        _history_context = ""
                        if history_messages:
                            _hparts = []
                            for _hm in list(history_messages)[-6:]:
                                _hrole = getattr(_hm, 'type', '') or getattr(_hm, 'role', '')
                                _hcontent = str(getattr(_hm, 'content', '') or '').strip()
                                if not _hcontent:
                                    continue
                                if _hrole in ('user', 'human'):
                                    _hparts.append(f"用户：{_hcontent[:200]}")
                                else:
                                    # 助手历史只取首行摘要（去掉结果表格等明细，避免污染下一轮决策——
                                    # 此前把大段「| 工单号 |…」明细塞进 prompt，导致 LLM 误判「已查过/没有找到」直接 done）
                                    _first_line = _hcontent.split("\n")[0].strip()[:100]
                                    _hparts.append(f"助手：{_first_line}")
                            if _hparts:
                                _history_context = "对话上文（最近几轮）：\n" + "\n".join(_hparts) + "\n\n"
                        for _round in range(_max_rounds):
                            # 第一轮：L2 分类结果只作「工具名提示」，参数由 LLM 决策按 schema 填
                            # （理解归 LLM，不再硬编码 params={} 靠正则提取）；之后每轮 LLM 看结果再决策。
                            # 流式决策：reasoning 片段实时转发（Think 块逐字流式，不等决策完成）
                            decision = None
                            async for _devt in self._decide_next_step(
                                original_message, decision_candidates, _results, model_name,
                                known_tool=(l2_name if _round == 0 else ""),
                                history_context=_history_context,
                                show_thinking=(_round == 0),
                            ):
                                if _devt[0] == "thinking":
                                    yield ("thinking", _devt[1])
                                else:
                                    decision = _devt[1]
                            _act = decision.get("action", "done")
                            _txt = decision.get("text", "")
                            if _act == "done":
                                # done：text 是 LLM 总结/追问，存下，由收尾统一决定
                                # （执行过工具 → _format_result 展示数据 + text 补充；否则直接 text）
                                _final_text = _txt
                                break
                            # tool/ask 轮的 text（「正在查询…」）不再单独 yield 成 text 块：
                            # reasoning（Think）已覆盖「我要做什么」，单独成 text 会与 done 结论两段重复（对齐 DSH）
                            if _act == "ask":
                                # 反问用户：复用澄清轮内挂起
                                _clarify_event = self._prepare_clarify(session_id)
                                # DSH 式选择：从最近一次查询结果（多条候选）里确定性提取候选选项，
                                # 前端渲染成可点击按钮，用户点选编码而非手敲（对齐 dsh ask_user_question）。
                                _ask_options = []
                                # 只用「最近一次」查询结果：用户选定实体后，不再回退到更早的候选
                                # （如选完物料又弹「共有 8 条候选」）；最近一次查询若 0 条则无候选可点。
                                _last_query = None
                                for _r in reversed(_results):
                                    _tool = _r.get("tool") or ""
                                    if _tool and _tool not in ("_user_reply", "_done"):
                                        _last_query = _r
                                        break
                                if _last_query:
                                    _recs = _last_query.get("records") or []
                                    if len(_recs) > 1:
                                        _first = _recs[0]
                                        _pk_key = next((k for k in ("materialCode", "code", "routingCode", "id") if k in _first), "")
                                        _name_key = next((k for k in ("name", "materialName", "routingName") if k in _first), "")
                                        for _rec in _recs[:8]:
                                            _label = str(_rec.get(_pk_key, "") or "").strip() if _pk_key else ""
                                            _desc = str(_rec.get(_name_key, "") or "").strip() if _name_key else ""
                                            if _label and _label != "-":
                                                _ask_options.append({"label": _label, "description": _desc})
                                # ── 问卷预查注入（对齐 DSH「先查参考数据再问，选项内嵌」）──
                                # groups 题对应 ref 参数（物料/工艺路线等）且无候选时，
                                # 按 action 签名的 conceptPropertyRef 确定性预查前 8 条注入 options，
                                # 用户在问卷里直接点选编码而非手敲
                                _groups = decision.get("groups") or []
                                if _groups:
                                    from app.services.action_executor import action_executor as _aex
                                    # 目标写工具签名：优先 decision.tool，兜底=全部候选写工具（非 _query）
                                    _target_sigs = []
                                    _dt = decision.get("tool") or ""
                                    if _dt and _dt in _aex._sigs:
                                        _target_sigs.append(_aex._sigs.get(_dt, {}))
                                    else:
                                        _target_sigs = [
                                            _aex._sigs.get(c.get("name", ""), {}) or {}
                                            for c in decision_candidates
                                            if not str(c.get("name", "")).endswith("_query")
                                            and c.get("name") in _aex._sigs
                                        ]
                                    for _g in _groups:
                                        _pname = _g.get("param") or ""
                                        _lbl = _g.get("label", "") or ""
                                        # param 兜底：LLM 未输出 param 时按参数中文 label 前缀匹配
                                        #（「物料编码是多少？」含参数 label「物料编码」→ materialCode）
                                        _match_sig = None
                                        _pp = None
                                        for _tsig in _target_sigs:
                                            for _p in _tsig.get("params", []):
                                                if _pname and _p.get("name") == _pname:
                                                    _match_sig, _pp = _tsig, _p
                                                    break
                                                _pl = str(_p.get("label") or "")
                                                if not _pname and _pl and _pl in _lbl:
                                                    _pname, _match_sig, _pp = _p.get("name"), _tsig, _p
                                                    _g["param"] = _pname
                                                    break
                                            if _match_sig:
                                                break
                                        if not _match_sig or not _pp:
                                            continue
                                        _ref = (_pp or {}).get("conceptPropertyRef", "")
                                        if not _ref or "." not in _ref:
                                            continue
                                        _ref_concept = _ref.split(".", 1)[0]
                                        # ref 指向自身概念（如 WorkOrder_create.quantity → WorkOrder.quantity，
                                        # 执行后回填展示用）不是外部实体引用——不注入候选（数量/日期自由输入）
                                        if _ref_concept == (_match_sig.get("conceptName") or ""):
                                            continue
                                        _c_tool = next((c.get("name") for c in decision_candidates
                                                        if str(c.get("name", "")).startswith(f"{_ref_concept}_query")), "")
                                        if not _c_tool or _g.get("options"):
                                            continue
                                        try:
                                            _qres = await _aex.execute_structured_async(
                                                _c_tool, {"_limit": 8}, user_id=user_id)
                                            _recs = _qres.get("records") or []
                                            _pk_key = next((k for k in ("materialCode", "routingCode", "code", "id", "name")
                                                            if _recs and k in _recs[0]), "")
                                            _name_key = next((k for k in ("name", "materialName", "routingName", "description")
                                                              if _recs and k in _recs[0]), "")
                                            _opts = []
                                            for _rec in _recs[:8]:
                                                _lbl2 = str(_rec.get(_pk_key, "") or "").strip()
                                                _dsc = str(_rec.get(_name_key, "") or "").strip()
                                                if _lbl2 and _lbl2 != "-":
                                                    _opts.append({"label": _lbl2, "description": _dsc})
                                            if _opts:
                                                _g["options"] = _opts
                                                log.info(f"[{self.name}] 问卷预查注入: {_pname} ← {_c_tool} 命中 {len(_opts)} 条候选")
                                        except Exception as _pe:
                                            log.warning(f"[{self.name}] 问卷预查失败 {_pname}: {_pe}")
                                yield ('clarify_required', _json.dumps({
                                    "reason": "loop_ask", "question": _txt or "请补充信息",
                                    "tool": "", "action_label": "",
                                    "options": _ask_options,
                                    # 逐题问卷（DSH 式）：decision 输出 groups 时透传，
                                    # 前端 ClarifyTakeoverBar 渲染 1/N 逐题收集（缺失参数逐项问）
                                    "groups": _groups,
                                }, ensure_ascii=False))
                                _c2, _reply, _selected, _custom = await self._wait_for_clarify(session_id, _clarify_event)
                                if _c2:
                                    yield ('content', "操作已取消。")
                                    yield ('execution_done', _json.dumps({"cancelled": True}))
                                    _cancelled = True
                                    break
                                # 用户点选（确定性值）优先；自由输入 custom 次之；文本 reply 兜底
                                _answer = ""
                                if _selected:
                                    _answer = "，".join(str(s) for s in _selected)
                                elif _custom and _custom.strip():
                                    _answer = _custom.strip()
                                elif _reply and _reply.strip():
                                    _answer = _reply.strip()
                                if _answer:
                                    _results.append({"tool": "_user_reply", "result": _answer})
                                continue
                            # action == tool：执行单个工具
                            _tool_name = decision.get("tool", "")
                            if not _tool_name or _tool_name not in {c.get("name") for c in decision_candidates}:
                                # 工具名不在候选里 → 视为 done，避免幻觉工具
                                _final_text = _txt or "已完成。"
                                break
                            # 防重复：连续查同一查询工具（上一轮已查到结果）→ 不再查，
                            # 直接交给已查结果格式化展示，避免 LLM 反复查同一物料。
                            if _tool_name.endswith("_query") and _tool_name == _last_query_tool:
                                _final_text = _txt or ""
                                break
                            yield ('route_match', _json.dumps({
                                "method": "loop", "tool": _tool_name,
                                "confidence": 1.0,
                                "concept_label": next((c.get("concept_label", "") for c in decision_candidates if c.get("name") == _tool_name), ""),
                                "action_label": next((c.get("label", "") for c in decision_candidates if c.get("name") == _tool_name), _tool_name),
                            }))
                            _sout: dict = {}
                            async for _evt in self._execute_single_tool(
                                tool_name=_tool_name, message=message, original_message=original_message,
                                session_id=session_id, user_id=user_id, _is_complete=_is_complete,
                                _sort_reply=_sort_reply, _track=_track, _t_start=_t_start,
                                decision_params=decision.get("params", {}) or {}, out=_sout,
                            ):
                                yield _evt
                            if "tool_result" not in _sout:
                                # 确认取消/澄清未决/委托 → 结束循环
                                _cancelled = True
                                break
                            _tr = _sout["tool_result"]
                            _results.append({
                                "tool": _tool_name,
                                "result": _tr.get("result", ""),
                                "rowCount": _tr.get("rowCount", 0),
                                "actionType": _tr.get("actionType", "query"),
                                "records": _tr.get("records", []) or [],
                                "columns": _tr.get("columns", []) or [],
                            })
                            # 记录 routing + tool_result（0 条也要给 _format_result 做诊断分析）
                            _final_routing = _sout.get("routing_result")
                            _final_tool_result = _tr
                            # 写操作（create/update/delete）执行完成 → 立即结束循环去格式化：
                            # 写是终点动作，不再进入下一轮 LLM 决策，否则 LLM 会重复决策同一写操作
                            #（实测「删除工单」被重复执行 4 次：每轮都重新查证+删除）。
                            if not _tool_name.endswith("_query"):
                                break
                            # 查询工具返回 0 条 → 确定性诚实告知（执行归确定性，不依赖 LLM 是否 done），
                            # 避免「查不到 → LLM 又重复查同一工具」的死循环/反复弹框。
                            if _tool_name.endswith("_query") and _tr.get("rowCount", 0) == 0:
                                # DSH 对齐：0 条不强制 break、也不记 _last_query_tool，
                                # 把结果喂回下一轮 LLM 反思（LLM 看到 0 条 + 诊断，自己决定调整参数重查
                                # 或诚实告知「没有找到」）。防死循环靠 _max_rounds 上限 + prompt「0 条诚实告知、不要反复查」。
                                pass
                            else:
                                # 查询工具查到结果 → 记录，防止下一轮重复查同一工具
                                if _tool_name.endswith("_query"):
                                    _last_query_tool = _tool_name
                        # 循环结束：执行过工具则用 _format_result 格式化展示结果数据（不能丢数据），
                        # LLM done 的 text 仅作「总结/追问」追加；未执行工具才直接用 text。
                        if not _cancelled:
                            if _final_tool_result is not None:
                                # 执行过工具：格式化展示结果（含查询数据/操作结果）
                                _fr = _final_routing or intent_router.route_explicit(l2_name, message)
                                async for _evt in self._format_result(
                                    routing_result=_fr, tool_result=_final_tool_result,
                                    message=message, session_id=session_id, model_name=model_name,
                                ):
                                    yield _evt
                                # LLM 的总结/追问（如「已查到N条，需要筛选吗」）作为补充，不替代数据
                                if _final_text:
                                    yield ('content', _final_text)
                            elif _final_text:
                                yield ('content', _final_text)
                                yield ('execution_done', _json.dumps({"method": "loop", "rounds": _round + 1}))
                            else:
                                yield ('content', "已处理完成。")
                                yield ('execution_done', _json.dumps({"method": "loop"}))
                        return
                    async for _evt in self._execute_multi_step(
                        message=message, original_message=original_message,
                        session_id=session_id, model_name=model_name,
                        enable_thinking=enable_thinking, history_messages=history_messages,
                        user_id=user_id, _is_ask_followup=_is_ask_followup,
                        candidate_list=candidate_list, onto_tools=onto_tools,
                        concept_names=concept_names, _track=_track, _t_start=_t_start,
                    ):
                        yield _evt
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


    async def _is_compound_intent(self, message: str, model_name: Optional[str]) -> bool:
        """LLM 判断消息是否复合任务（多个动作意图，需分多步完成）。

        阶段 B「理解层强化」：复合任务判断归 LLM（理解归 LLM）；
        动作词计数只作为 LLM 失败时的确定性降级兜底（异常降级），不再作为主判断。
        另加确定性快速预判：明显单动作且无连接词 → 直接非复合，跳过 LLM（省一次调用，降延迟）。
        """
        import asyncio
        from app.agents.settings.model import MODEL_CONFIG
        from app.services.llm_service import llm_service

        # 确定性快速预判：单动作且无连接词 → 直接非复合（省一次 LLM 调用）
        _action_verbs = ("创建", "新增", "新建", "更新", "修改", "删除",
                         "排程", "插单", "查询", "查", "分析", "统计")
        _connectors = ("并", "然后", "顺便", "再", "以及", "同时", "后")
        # 去重计数：长词优先，避免「查」与「查询」父子词重复计数（如「查询工单」被算成 2 个动作）
        _hits = 0
        _msg_hits = message
        for _v in sorted(_action_verbs, key=len, reverse=True):
            if _v in _msg_hits:
                _hits += 1
                _msg_hits = _msg_hits.replace(_v, "", 1)
        _has_conn = any(_c in message for _c in _connectors)
        if _hits < 2 and not (_hits >= 1 and _has_conn):
            return False

        _prompt = (
            "判断用户消息是否包含多个动作意图（需要分多步完成的复合任务）。\n"
            "复合任务示例：「创建工单并排程」「先查库存再下单」「删除旧数据并更新记录」\n"
            "单步任务示例：「查询工单」「创建销售订单」「删除工单 WO-001」「分析库存」\n"
            f"用户消息：{message}\n"
            "只输出 true 或 false。"
        )
        try:
            _judge = await asyncio.wait_for(
                llm_service.chat_sync(
                    message=_prompt,
                    system_prompt="你是复合任务判别器，只输出 true 或 false。",
                    model_name=MODEL_CONFIG.get("decision_model"),
                ),
                timeout=5.0,
            )
            return 'true' in str(_judge).strip().lower()
        except Exception:
            # 异常降级：确定性动作词计数兜底（主判断已归 LLM）
            return _hits >= 2

    async def _execute_chat(
        self, *, l2_confidence, session_id, original_message, model_name, enable_thinking, history_messages, web_search, _track, _t_start,
    ) -> AsyncGenerator[tuple, None]:
        """执行 chat 决定：闲聊/寒暄/讨论，自由对话，不套查询模板、不走工具路由。"""
        import json as _json
        import time as _t
        from app.services.llm_service import llm_service
        from app.core.prompts import DEFAULT_SYSTEM_PROMPT

        # 闲聊/寒暄/讨论：直接自由对话，不套查询模板、不走工具路由
        yield ('route_match', _json.dumps({
            "method": "chat", "tool": "chat", "confidence": l2_confidence,
            "concept_label": "自由对话",
        }))
        _track("CHAT", "chat", l2_confidence, session_id, original_message, elapsed_ms=int((_t.time() - _t_start) * 1000))
        # 注入真实业务域能力清单：「你有什么功能」基于本体编译产物回答，
        # 而非通用助手话术（此前 CHAT 直答只有 DEFAULT_SYSTEM_PROMPT，与业务域完全脱节）
        _chat_prompt = DEFAULT_SYSTEM_PROMPT + _chat_capability_section()
        async for _ct, _cc in llm_service.chat_stream(
            message=original_message, session_id=session_id, model_name=model_name,
            system_prompt=_chat_prompt, enable_thinking=enable_thinking,
            history_messages=history_messages, use_agent=False, web_search=web_search,
        ):
            yield (_ct, _cc)
        yield ('execution_done', _json.dumps({"method": "chat", "totalSteps": 1}))
        return



    async def _execute_ask(
        self, *, original_message, session_id, user_id, model_name,
        l2_confidence, candidate_list, _track, _t_start,
    ) -> AsyncGenerator[tuple, None]:
        """执行 ask 决定：能力发现 + 反问澄清（确定性，本体驱动）。"""
        import json as _json
        import time as _t
        from app.services.ontology_service import ontology_service
        from app.services.tool_registry import (
            tool_registry, filter_writes_by_verb, build_capability_fallback,
        )

        # ── 能力发现 + 反问澄清（确定性，本体驱动，替代"暂未开放"）──
        # 用全局动作签名（当前 namespace 所有动作），而非当前 agent 的候选，
        # 避免路由到分析类 agent 时只见其查询能力、漏掉业务写操作。
        _write_ops = []
        _seen = set()
        try:
            from app.services.tool_registry import tool_registry
            tool_registry.ensure_loaded(ontology_service)
            _all_tools = tool_registry.get_writes()
        except Exception:
            _all_tools = []
        for _tool in _all_tools:
            _fn = _tool.get('name') or ''
            if not _fn or _fn.startswith('mcp_'):
                continue
            _key = (_tool.get('concept_label') or '', _tool.get('label') or _fn)
            if _key in _seen:
                continue
            _seen.add(_key)
            _write_ops.append({
                'name': _fn,
                'label': _tool.get('label') or _fn,
                'concept_label': _tool.get('concept_label') or _tool.get('concept_name') or '',
                'concept_name': _tool.get('concept_name') or '',
            })
        # 动作动词过滤：用户说"创建"就只留 create 类，避免 186 项全列
        from app.services.tool_registry import filter_writes_by_verb
        _write_ops = filter_writes_by_verb(_write_ops, original_message)
        # 注意：模糊写意图反问澄清是「正常交互」，不是异常，不创建异常工单；
        # 异常工单只在真正的系统异常（except 块）或写操作失败需人工复核时创建。
        if _write_ops:
            # 写操作少（<=6）→ 确定性反问（省一次 LLM，降延迟）；多 → LLM 反问精选最相关
            if len(_write_ops) <= 6:
                _lines = "\n".join(
                    f"• {c.get('label') or c.get('name')}（{c.get('concept_label') or c.get('concept_name')}）"
                    for c in _write_ops
                )
                _reply = (
                    f"你想「{original_message}」吗？我可以帮你：\n{_lines}\n"
                    f"请点击上面的选项，或告诉我具体要做什么。"
                )
            else:
                # LLM 理解意图 → 生成自然语言反问 + 推荐最相关的几个（理解归 LLM）；
                # 只能从本体操作里选（本体是能力边界），失败回退确定性精简清单。
                _reply = None
                try:
                    from app.services.llm_service import llm_service as _llm3
                    _choices = "、".join(c.get('label') or c.get('name') for c in _write_ops)
                    _reply = await _llm3.chat_sync(
                        message=f"用户想「{original_message}」，但本体没有直接对应的操作。\n"
                                f"本体的写操作有：{_choices}\n"
                                f"请先用一句话反问用户澄清意图（如「你要创建哪种单据？」），"
                                f"再列出你认为最相关的 3~5 个操作，每行一个「• 操作名」。"
                                f"只能从上面列出的操作里选，不要编造。",
                        system_prompt="你是企业智能助手，帮用户澄清模糊的操作意图。",
                        model_name=model_name,
                    )
                    if not _reply or len(str(_reply).strip()) < 5:
                        _reply = None
                except Exception:
                    _reply = None
                if not _reply:
                    from app.services.tool_registry import build_capability_fallback
                    _reply = build_capability_fallback(original_message, _write_ops)
        else:
            _reply = f"抱歉，「{original_message}」当前没有对应的写操作。我可以帮你查询和分析数据，试试换个说法？"
        yield ('content', _reply)
        yield ('done', _json.dumps({
            "unsupported": True,
            "capabilities": [c.get('label') or c.get('name') for c in _write_ops],
            # 结构化点选选项（前端渲染成可点击卡片，点击后作为消息发送触发对应写操作）
            "quick_replies": [
                {
                    "label": c.get('label') or c.get('name'),
                    "description": (c.get('concept_label') or c.get('concept_name') or ""),
                }
                for c in _write_ops[:6]
            ],
        }))
        yield ('data_source', _json.dumps({"source": "none", "hint": "capability_discovery"}))
        _track("UNSUPPORTED", "llm", l2_confidence, session_id, original_message, elapsed_ms=int((_t.time() - _t_start) * 1000))
        return



    async def _decide_next_step(
        self, message: str, candidate_list: list, results: list,
        model_name: Optional[str], known_tool: str = "",
        history_context: str = "", show_thinking: bool = False,
    ) -> dict:
        """循环决策（理解归 LLM）：看观察（消息+候选工具+已执行结果），产出下一步决定。

        对齐 DSH 的「LLM 每轮决策」：每轮 LLM 看当前状态，决定
        - tool  → 继续调用某个工具（附关键信息 text，说明这一步要做什么/结果如何）
        - ask   → 信息不足，反问用户
        - done  → 任务完成，输出最终关键信息 text

        返回 {"action": "tool"|"ask"|"done", "tool": 工具名, "params": {...}, "text": 中间关键信息}

        参数（理解层）也归 LLM：prompt 里给足工具参数 schema（name/label/type/required/
        枚举），LLM 按 schema 填 params，不再靠正则猜字段名。
        """
        import json as _json
        from app.agents.settings.model import MODEL_CONFIG
        from app.services.llm_service import llm_service
        from app.services.action_executor import action_executor as _ae_decide

        # 确保动作签名（含参数 schema）已加载，供 LLM 按 schema 填 params
        _ae_decide._ensure_loaded()

        # 工具名列表只作范围提示（详细签名/参数由 function calling 的 tools schema 提供，
        # 避免 prompt 与 tools 双份冗余、拖慢首 token——对齐 DSH：prompt 只讲规则，工具靠 schema）
        options = "、".join(c.get("name", "") for c in candidate_list if c.get("name")) or "(无可用工具)"

        results_text = ""
        if results:
            lines = []
            for r in results:
                tool = r.get("tool", "")
                rowCount = r.get("rowCount", 0)
                # 截断放宽到 300 字 + 显式带条数，让 LLM 看到「查到了几条 + 前几个候选编码」，
                # 避免只看到第一条就误以为唯一（如模糊查物料 10 条却只认 811474）。
                brief = str(r.get("result", ""))[:300].replace("\n", " ")
                cnt = f"（{rowCount} 条）" if rowCount else ""
                lines.append(f"- [{tool}]{cnt} {brief}")
            results_text = "已执行的工具与结果：\n" + "\n".join(lines) + "\n\n"

        _known_hint = ""
        if known_tool:
            _known_hint = f"（已初步识别工具为 {known_tool}，如无异议请沿用它并填写其参数）"

        prompt = (
            "你是企业智能助手。\n\n"
            f"用户消息：{message}\n\n"
            f"{history_context}"
            f"可用工具（调用名）：{options}\n\n"
            f"{results_text}"
            f"{_known_hint}"
            "规则：\n"
            "1. 需要查询/创建/删除/修改数据时，必须调用对应工具实际执行，不要用文字描述代替调用。\n"
            "2. 信息已足够 → 直接回答；信息不足 → 反问用户（此时不调工具）。\n"
            "3. 参数字段名必须来自该工具的参数列表，值只填用户消息/结果里明确出现的，不编造；"
            "模糊引用（如「38开头」）→ 先调 *_query，用 _fuzzy=值、_fuzzy_op=prefix/contains 查清候选。\n"
            "4. 写操作前查到多条候选 → 反问让用户选定具体编码，不把模糊值填进写操作。\n"
            "5. 用户当前是明确指令（查询/删除等）→ 必须重新执行，不因上文已有结果就跳过。\n"
            "6. 结果为空/未找到 → 诚实告知，不反复调查询工具。\n"
            "7. 涉及具体实体是否存在，先调 *_query 再下结论，不凭上文臆断。\n"
            "8. 识别延续上文未完成任务（如「用380000」是继续创建工单），先查补 ref 再写。\n"
            "9. 用户已按「问法：值」回答问卷 → 直接映射参数执行，不重复问。\n"
            "10. text 中文简洁；done 的 text 是结果结论，禁止「正在查询/请稍候」进度话术。\n"
            "11. 用户要求的操作在可用工具里没有对应项 → 直接回答暂不支持，不要强行调用不相关的工具。\n"
        )
        try:
            from app.services.action_executor import action_executor as _aex_fc
            # ── 构造 function calling 工具 schema（候选工具 → OpenAI function 格式）──
            _fc_tools = []
            for _c in candidate_list:
                _cn = _c.get("name", "")
                if not _cn:
                    continue
                _sig = _aex_fc._sigs.get(_cn, {}) or {}
                _props = {}
                _required = []
                for _p in _sig.get("params", []):
                    _ptype = str(_p.get("type", "string")).lower()
                    _otype = "string"
                    if _ptype in ("int", "integer", "number", "float"):
                        _otype = "number"
                    elif _ptype in ("bool", "boolean"):
                        _otype = "boolean"
                    _prop = {"type": _otype, "description": _p.get("label") or _p.get("name")}
                    # 枚举值补进 schema（原在 prompt 里的枚举说明已精简，这里补齐避免丢失）
                    _ev = _p.get("enumValues")
                    if isinstance(_ev, (list, tuple)) and _ev:
                        _prop["enum"] = [str(e) for e in _ev]
                    _props[_p.get("name")] = _prop
                    if _p.get("required"):
                        _required.append(_p.get("name"))
                _fc_tools.append({
                    "type": "function",
                    "function": {
                        "name": _cn,
                        "description": (_c.get("description") or _c.get("label") or _cn)[:300],
                        "parameters": {"type": "object", "properties": _props, "required": _required},
                    },
                })
            # ── function calling：模型自由选工具或直接回答，reasoning 是任务推理（DSH 式，讲人话不元思考）──
            _reasoning_parts = []
            _content_parts = []
            _tool_calls = []
            async for _kind, _piece in llm_service.chat_stream_fc(
                message=prompt,
                system_prompt="你是制造业智能助手，根据用户意图从可用工具中选择合适的工具执行，或直接回答用户。",
                tools=_fc_tools,
                model_name=model_name or MODEL_CONFIG.get("decision_model"),
            ):
                if _kind == "thinking":
                    _reasoning_parts.append(_piece)
                    # 仅第一轮流式显示 think（reasoning 是任务推理，讲人话）；
                    # 后续轮 reasoning 不显示（done 轮与首轮重复，DSH 后续轮是「已查到，总结」）
                    if show_thinking:
                        yield ("thinking", _piece)
                elif _kind == "content":
                    _content_parts.append(_piece)
                else:
                    _tool_calls = _piece
            _reasoning = "".join(_reasoning_parts)
            _content = "".join(_content_parts).strip()
            if _tool_calls:
                _tc = _tool_calls[0]
                _dec = {
                    "action": "tool",
                    "tool": _tc.get("name", ""),
                    "params": _tc.get("arguments", {}) or {},
                    "text": _content or "",
                    "groups": [],
                    "reasoning": _reasoning,
                }
            elif _content:
                _dec = {"action": "done", "tool": "", "params": {}, "text": _content, "groups": [], "reasoning": _reasoning}
            else:
                _dec = {"action": "done", "tool": "", "params": {}, "text": "", "groups": [], "reasoning": _reasoning}
            log.info(f"[{self.name}] 循环决策: action={_dec['action']} tool={_dec['tool']!r} params={_dec['params']} text={_dec['text'][:40]!r}")
            yield ("decision", _dec)
        except Exception as e:
            log.warning(f"[{self.name}] 循环决策失败，回退 done: {e}")
            # 第一轮（有 L2 工具名提示）失败时回退到该工具，params 空由执行层兜底，
            # 避免 LLM 故障导致「本该执行工具却直接 done」。
            if known_tool:
                yield ("decision", {"action": "tool", "tool": known_tool, "params": {}, "text": "", "groups": [], "reasoning": ""})
            else:
                yield ("decision", {"action": "done", "tool": "", "params": {}, "text": "", "groups": [], "reasoning": ""})

    async def _execute_single_tool(
        self, *, tool_name, message, original_message, session_id, user_id,
        _is_complete, _sort_reply, _track, _t_start, out, decision_params=None,
    ) -> AsyncGenerator[tuple, None]:
        """执行单个工具（确认→参数→治理→执行→验证），不含格式化。

        写 out['tool_result']；确认取消/澄清未决/委托则提前 return 不写。
        供循环（_run_tool_loop）每步调用，格式化由循环结束后统一做。

        decision_params：循环决策 LLM 产出的参数（理解层）。传入 _resolve_params
        作为参数初始值，正则 extract_params 只在其为空时兜底（执行归确定性）。
        """
        import json as _json
        from app.services.intent_router import intent_router

        routing_result = intent_router.route_explicit(tool_name, message)
        if not routing_result.has_handler:
            yield ('content', f"抱歉，「{original_message}」操作暂未开放。当前仅支持查询与分析类操作。")
            yield ('done', _json.dumps({"unsupported": True}))
            return

        # ── 确认 + 参数提取 ──
        _pout: dict = {}
        async for _evt in self._resolve_params(
            routing_result=routing_result, message=message, original_message=original_message,
            session_id=session_id, user_id=user_id, _is_complete=_is_complete,
            _sort_reply=_sort_reply, _track=_track, _t_start=_t_start,
            decision_params=decision_params, out=_pout,
        ):
            yield _evt
        if "params" not in _pout:
            return
        params = _pout["params"]

        # ── 治理流水线 ──
        try:
            from app.agents.governance import governance_pipeline
            from app.services.action_executor import action_executor as _ae_gov
            _sig_gov = _ae_gov._sigs.get(tool_name, {}) or {}
            _fn = tool_name or ""
            if _fn.endswith("_query"):
                _out_type = "query"
            elif _fn.endswith("_delete"):
                _out_type = "delete"
            elif _fn.endswith("_findSimilar"):
                _out_type = "similarity"
            else:
                _out_type = _sig_gov.get("outputType", "") or "write"
            _gov_report = await governance_pipeline.evaluate(
                tool_name, _out_type, sigs=_ae_gov._sigs,
                user_id=user_id,
                authorized_roles=routing_result.authorized_roles or [],
                concept_name=_sig_gov.get("conceptName", ""),
                params=params,
                requires_confirmation=routing_result.requires_confirmation,
            )
            log.info(f"[{self.name}] 治理流水线: {_gov_report.summary()}")
        except Exception:
            pass

        # ── 执行 + 反思 ──
        _eout: dict = {}
        async for _evt in self._execute_and_reflect(
            routing_result=routing_result, params=params, message=message,
            original_message=original_message, session_id=session_id, user_id=user_id, out=_eout,
        ):
            yield _evt
        tool_result = _eout["tool_result"]
        _tool_landing = _eout["tool_landing"]

        # ── 预警 + 审批门禁 ──
        _gout: dict = {}
        async for _evt in self._apply_write_gates(
            routing_result=routing_result, params=params, tool_result=tool_result,
            tool_landing=_tool_landing, session_id=session_id, user_id=user_id, out=_gout,
        ):
            yield _evt
        if "tool_result" not in _gout:
            return
        tool_result = _gout["tool_result"]

        # ── 写操作确定性验证 ──
        try:
            from app.agents.reflector import reflector as _reflector
            _verify = _reflector.verify_write_result(tool_result)
            if _verify.needs_review:
                tool_result["needs_review"] = True
                tool_result["verify_reason"] = _verify.reason
        except Exception:
            pass

        out["tool_result"] = tool_result
        # 关键字段确定性注入：把实际执行参数挂到 tool_result，供 _format_result 防幻觉
        #（写操作报告的关键字段只能复述这些真实值，不得由 LLM 编造）
        try:
            tool_result["_params"] = params
        except Exception:
            pass
        out["routing_result"] = routing_result

    async def _execute_tool(
        self, *, l2_name, l2_method, l2_confidence, message, original_message,
        session_id, user_id, model_name, enable_thinking, _is_complete,
        _sort_reply, candidate_list, rag_count, concept_names, _track, _t_start,
    ) -> AsyncGenerator[tuple, None]:
        """执行 tool 决定：单步工具（参数提取→确认→执行→格式化）。"""
        import json as _json
        import time as _t
        from app.core.tracing import span
        from app.services.intent_router import intent_router
        from app.services.action_executor import action_executor
        from app.services.ontology_service import ontology_service
        from app.services.llm_service import llm_service

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

        # ── 确认 + 参数提取 ──
        _pout: dict = {}
        async for _evt in self._resolve_params(
            routing_result=routing_result, message=message, original_message=original_message,
            session_id=session_id, user_id=user_id, _is_complete=_is_complete,
            _sort_reply=_sort_reply, _track=_track, _t_start=_t_start, out=_pout,
        ):
            yield _evt
        if "params" not in _pout:
            return
        params = _pout["params"]

        # ── 治理流水线：六闸门统一审计（工具边界/RBAC/数据权限/规则/风险/审批），留痕可观测 ──
        try:
            from app.agents.governance import governance_pipeline
            from app.services.action_executor import action_executor as _ae_gov
            _sig_gov = _ae_gov._sigs.get(routing_result.tool_name, {}) or {}
            _fn = routing_result.tool_name or ""
            # 后缀推断优先（对齐 tool_registry.collect_ontology），outputType 仅作兜底
            if _fn.endswith("_query"):
                _out_type = "query"
            elif _fn.endswith("_delete"):
                _out_type = "delete"
            elif _fn.endswith("_findSimilar"):
                _out_type = "similarity"
            else:
                _out_type = _sig_gov.get("outputType", "") or "write"
            _gov_report = await governance_pipeline.evaluate(
                routing_result.tool_name, _out_type, sigs=_ae_gov._sigs,
                user_id=user_id,
                authorized_roles=routing_result.authorized_roles or [],
                concept_name=_sig_gov.get("conceptName", ""),
                params=params,
                requires_confirmation=routing_result.requires_confirmation,
            )
            log.info(f"[{self.name}] 治理流水线: {_gov_report.summary()}")
        except Exception:
            pass

        # ── 执行 + 反思 ──
        _eout: dict = {}
        async for _evt in self._execute_and_reflect(
            routing_result=routing_result, params=params, message=message,
            original_message=original_message, session_id=session_id, user_id=user_id, out=_eout,
        ):
            yield _evt
        tool_result = _eout["tool_result"]
        _tool_landing = _eout["tool_landing"]

        # ── 预警 + 审批门禁 ──
        _gout: dict = {}
        async for _evt in self._apply_write_gates(
            routing_result=routing_result, params=params, tool_result=tool_result,
            tool_landing=_tool_landing, session_id=session_id, user_id=user_id, out=_gout,
        ):
            yield _evt
        if "tool_result" not in _gout:
            return
        tool_result = _gout["tool_result"]

        # ── 反馈闭环：写操作确定性验证（失败标记需人工复核，回滚由轨迹回滚人工触发）──
        try:
            from app.agents.reflector import reflector as _reflector
            _verify = _reflector.verify_write_result(tool_result)
            log.info(f"[{self.name}] 反馈验证: {routing_result.tool_name} "
                     f"ok={_verify.ok} reason={_verify.reason}")
            if _verify.needs_review:
                log.warning(f"[{self.name}] 写操作需人工复核: {routing_result.tool_name} "
                            f"({_verify.reason})")
                tool_result["needs_review"] = True
                tool_result["verify_reason"] = _verify.reason
        except Exception:
            pass

        # ── LLM 格式化 ──
        async for _evt in self._format_result(
            routing_result=routing_result, tool_result=tool_result,
            message=message, session_id=session_id, model_name=model_name,
        ):
            yield _evt

    async def _resolve_params(
        self, *, routing_result, message, original_message, session_id,
        user_id, _is_complete, _sort_reply, _track, _t_start, out,
        decision_params=None,
    ) -> AsyncGenerator[tuple, None]:
        """确认检查 + 参数提取，写 out['params']；取消/委托/澄清则提前 return 不写。

        decision_params：循环决策 LLM 产出的参数（理解层）。非空时直接作为参数
        初始值，跳过正则 extract_params / LLM 填槽（理解不再重复提取）；为空时
        走原正则+LLM 兜底。
        """
        import json as _json
        import time as _t
        from app.services.intent_router import intent_router
        from app.services.action_executor import action_executor
        from app.services.ontology_service import ontology_service

        # ── Confirmation check ──
        if routing_result.requires_confirmation:
            # ── 确认路由：inline vs 委托审批 ──
            from app.services.auth_service import auth_service as _auth_svc
            user_roles = await _auth_svc.get_effective_roles(user_id) if user_id else set()
            required_roles = set(routing_result.authorized_roles or [])
            needs_delegation = required_roles and not (user_roles & required_roles)

            # 参数完全归 LLM 决策（DSH react）：decision_params 是唯一参数来源，
            # 不再用正则 extract_params / extract_params_llm 机械填槽兜底；
            # 缺必填字段由下方澄清循环（missing_required）处理。
            _copy_source = None
            _copy_source_label = ""
            if decision_params:
                prefill = {k: v for k, v in decision_params.items()
                           if not k.startswith('_') and v not in (None, '')}
            else:
                prefill = {}
            # L2: resolve entity references (列表查询时跳过历史上下文)
            prefill = await intent_router.resolve_entities(
                original_message if _is_complete else message,
                routing_result.tool_name, prefill,
            )
            # L3: fall back to LLM params for anything still empty
            for k, v in (routing_result.params or {}).items():
                if k not in prefill or not prefill.get(k):
                    prefill[k] = v
            # 提前取动作签名（后续污染清除 + 源预填复用）
            _sig = action_executor._sigs.get(routing_result.tool_name, {})
            _concept_name = _sig.get("conceptName", "")
            # 确定性兜底：数字字段被日期年份污染时清除（如「完工日期2026-09-30」把 2026 误填进 quantity）。
            # 在源预填之前清除，这样复制源能回填正确的源值。
            _date_fields = {p.get('name') for p in _sig.get('params', []) if p.get('type') in ('date', 'datetime')}
            _num_fields = {p.get('name') for p in _sig.get('params', []) if p.get('type') in ('int', 'float', 'number')}
            for _df in _date_fields:
                _dv = prefill.get(_df)
                _year = None
                if isinstance(_dv, str):
                    import re as _re_dt
                    _m_dt = _re_dt.match(r'(\d{4})[-/]', _dv)
                    if _m_dt:
                        _year = int(_m_dt.group(1))
                if _year:
                    for _nf in _num_fields:
                        _nv = prefill.get(_nf)
                        try:
                            if int(float(_nv)) == _year:
                                log.info(f"[{self.name}] 清除日期污染: {_nf}={_nv} 等于日期年份 {_year}")
                                prefill.pop(_nf, None)
                        except (TypeError, ValueError):
                            pass
            # L3.5 源实体预填（执行归确定性）：
            # LLM 已理解出「复制/参考某实体」的源编码 _copy_source → 查源实体，
            # 把源实体的可复制字段（= action 入参）预填；源编码不作为新单编码（系统生成）。
            _concept = ontology_service.get_concept(_concept_name)
            if _concept:
                _pk = next((p["name"] for p in _concept.get("properties", []) if p.get("isPrimary")), None)
                # 编辑类动作（update/delete）：用户给主键 → 查现有实体预填表单
                _src_val = _copy_source or (str(prefill.get(_pk)) if _pk and prefill.get(_pk) else None)
                if _src_val:
                    from app.services.data_backend import data_backend
                    _existing = await data_backend.resolve_entity(_concept_name, _src_val)
                    if _existing:
                        # 只预填 action 参数定义的字段（可复制字段 = action 入参）
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
                        if _copy_source:
                            _copy_source_label = _existing.get('statusDisplay') or _existing.get('materialName') or ''
                            prefill.pop(_pk, None)
                            log.info(f"[{self.name}] 复制源预填: {_concept_name} {_copy_source} → {prefill}")
            # L4: ontology graph traversal — enrich params + context
            enriched = await intent_router.enrich_params(routing_result.tool_name, prefill)
            # 复制源留痕：写入 context 供前端「已识别关联信息」展示
            if _copy_source:
                enriched.setdefault('context', {})['复制源'] = {
                    "entity": {"label": _copy_source, "name": _copy_source_label},
                    "label": "复制自",
                }
            param_schema = await intent_router.get_param_schema(routing_result.tool_name)

            # 写操作落点 + 可回滚性判定：C 级（外部 API 无撤销接口）加 irreversible 标记
            _landing = None
            if _concept_name:
                try:
                    from app.services.multi_system_backend import multi_system_backend
                    _landing = multi_system_backend.get_write_landing(_concept_name)
                except Exception:
                    _landing = None
            _irreversible = bool(_landing and _landing.get("is_api") and not _landing.get("reversible"))

            # ── 澄清前置（理解归 LLM + 循环一步）：确认前检测必填参数是否缺失 ──
            # 缺必填 → 轮内挂起问用户补（对齐 DSH ask_user_question，回答回来继续本轮循环）；
            # 参数齐 → 才进确认面板（核对+批准）。
            _params_now = dict(enriched.get('params', {}) or {})

            # 澄清循环：最多问 3 轮，每轮挂起等用户补充，LLM 从补充文本提取缺失字段合并
            for _clarify_round in range(3):
                # 每轮开头清理内部字段（resolve_entities 会带回 _cross_* 等中间产物）
                for _internal in ('_concept_entity', '_concept_name', '_cross_entity', '_cross_concept', '_cross_entity_name', '_fuzzy', '_fuzzy_op'):
                    _params_now.pop(_internal, None)
                _missing_required = []
                for _ps in param_schema:
                    if not _ps.get('required'):
                        continue
                    _v = _params_now.get(_ps.get('name'))
                    if _v is None or (isinstance(_v, str) and not _v.strip()):
                        _missing_required.append(_ps.get('label') or _ps.get('name'))
                if not _missing_required:
                    break
                _miss_text = "、".join(_missing_required)
                _clarify_q = (
                    f"「{routing_result.action_label}」还缺必填信息：{_miss_text}。"
                    f"请直接回复补充这些信息。"
                )
                log.info(f"[{self.name}] 写操作澄清前置: 缺 {_miss_text}，轮内挂起等补充")
                _clarify_event = self._prepare_clarify(session_id)
                yield ('clarify_required', _json.dumps({
                    "reason": "missing_required",
                    "question": _clarify_q,
                    "missing": _missing_required,
                    "tool": routing_result.tool_name,
                    "action_label": routing_result.action_label,
                    "round": _clarify_round + 1,
                }, ensure_ascii=False))
                # 问句只经 clarify_required.question 由前端 ClarifyCard 展示，
                # 不再 yield content（否则正文与澄清卡重复显示同一问句）。
                _cancelled, _reply, _selected, _custom = await self._wait_for_clarify(session_id, _clarify_event)
                if _cancelled:
                    yield ('content', "操作已取消。如需执行，请重新发送指令。")
                    yield ('execution_done', _json.dumps({"method": "clarify", "cancelled": True}))
                    _track("clarify", "llm", 0.0, session_id, original_message, elapsed_ms=int((_t.time() - _t_start) * 1000), extra={"reason": "missing_required_cancelled"})
                    return
                # 用户点选（确定性值）优先；自由输入 custom 次之；文本 reply 兜底
                _answer = ""
                if _selected:
                    _answer = "，".join(str(s) for s in _selected)
                elif _custom and _custom.strip():
                    _answer = _custom.strip()
                elif _reply and _reply.strip():
                    _answer = _reply.strip()
                if not _answer:
                    continue
                # 理解归 LLM：从用户补充文本里提取缺失字段（ref/int/date 都归 LLM 填槽），合并进参数。
                _supplement = await intent_router.extract_params_llm(_answer, routing_result.tool_name)
                for _k, _v in (_supplement or {}).items():
                    if _k.startswith('_'):
                        continue
                    if _v and (_k not in _params_now or not _params_now.get(_k)):
                        _params_now[_k] = _v
                # ref 字段（如工艺路线编码）走一次实体解析
                if _supplement:
                    _params_now = await intent_router.resolve_entities(_answer, routing_result.tool_name, _params_now)
                log.info(f"[{self.name}] 澄清补充合并: {_answer[:50]} → params={_params_now}")
            else:
                # 3 轮仍缺参数 → 放弃，提示
                _still_missing = [
                    _ps.get('label') or _ps.get('name') for _ps in param_schema
                    if _ps.get('required') and (
                        not _params_now.get(_ps.get('name'))
                        or (isinstance(_params_now.get(_ps.get('name')), str) and not _params_now.get(_ps.get('name')).strip())
                    )
                ]
                yield ('content', f"多次补充后仍缺少必填信息：{'、'.join(_still_missing)}。请重新发起指令并一次提供完整信息。")
                yield ('execution_done', _json.dumps({"method": "clarify", "cancelled": True}))
                return

            # 循环结束（参数已齐）：最后清理一次内部字段，确保确认面板与执行参数干净
            for _internal in ('_concept_entity', '_concept_name', '_cross_entity', '_cross_concept', '_cross_entity_name', '_fuzzy', '_fuzzy_op'):
                _params_now.pop(_internal, None)

            # ── 写操作 SOP：删/改前查证影响面（对齐 DSH「先查证再变更」）──
            _action_kind = routing_result.tool_name.split("_")[-1] if "_" in routing_result.tool_name else ""
            if _action_kind in ("delete", "update"):
                try:
                    from app.services.write_sop import precheck as _sop_precheck
                    _pk_name = next((p["name"] for p in (_concept or {}).get("properties", [])
                                     if p.get("isPrimary")), None) if _concept else None
                    _pk_name = _pk_name or ("code" if "code" in _params_now else ("id" if "id" in _params_now else ""))
                    _pk_val = _params_now.get(_pk_name) if _pk_name else None
                    if _pk_name and _pk_val:
                        _impact = await _sop_precheck(_concept_name, _pk_name, _pk_val, _action_kind)
                        if _impact and _impact.get("summary"):
                            yield ('content', f"\n\n{_impact['summary']}\n")
                except Exception as _se:
                    log.warning(f"[{self.name}] 写操作影响面查证异常: {_se}")

            # 始终先走内联确认，用户确认后再分流（params 存入挂起条目：
            # 前端确认 body 不带 params 时回退用原参数，避免确认后执行变成空参数）
            confirm_event = self._prepare_confirmation(
                session_id, message=original_message, action_label=routing_result.action_label,
                params=_params_now)
            yield ('confirm_required', _json.dumps({
                "tool": routing_result.tool_name,
                "action_label": routing_result.action_label,
                "concept_label": routing_result.concept_label,
                "params": _params_now,
                "param_schema": param_schema,
                "risk": "write",
                "irreversible": _irreversible,
                "landing": _landing or {},
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
                _pack = self._build_decision_pack(confirmed_params or enriched.get('params', {}), enriched.get('context', {}), param_schema, irreversible=_irreversible)
                yield ('confirm_delegated', _json.dumps({
                    "tool": routing_result.tool_name,
                    "action_label": routing_result.action_label,
                    "concept_label": routing_result.concept_label,
                    "params": confirmed_params or enriched.get('params', {}),
                    "param_schema": param_schema,
                    "risk": "write",
                    "irreversible": _irreversible,
                    "landing": _landing or {},
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

            out["params"] = confirmed_params
            return
        else:
            # 参数完全归 LLM 决策（DSH react）：decision_params 是唯一参数来源，
            # 不再用正则 extract_params / LLM 填槽兜底。
            if decision_params:
                # 查询工具的 _fuzzy/_fuzzy_op 是模糊搜索标记，要保留（否则「38开头」模糊查询失效）
                params = {k: v for k, v in decision_params.items()
                          if (not k.startswith('_') or k in ('_fuzzy', '_fuzzy_op')) and v not in (None, '')}
            else:
                params = {}
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
            out["params"] = params



    async def _execute_and_reflect(
        self, *, routing_result, params, message, original_message,
        session_id, user_id, out,
    ) -> AsyncGenerator[tuple, None]:
        """执行工具 + 查询 0 条反思兜底，写 out['tool_result']/out['tool_landing']。"""
        import json as _json
        from app.core.tracing import span
        from app.services.action_executor import action_executor

        # ── 执行前 preflight：规则/审批在 execute 之前判断（对齐 DSH pre-execute → approve → execute）──
        sig = action_executor._sigs.get(routing_result.tool_name, {})
        _pref = await action_executor.preflight(routing_result.tool_name, params, user_id=user_id)
        if _pref.get("blocked"):
            # 规则违规：不执行工具（不发出 tool_start），直接给违规结果
            tool_result = {
                "tool": routing_result.tool_name,
                "arguments": params,
                "result": "规则校验失败：\n" + "\n".join(
                    f"  • {v.message}" for v in _pref["violations"]
                ),
                "rowCount": 0, "source": "rule_engine", "actionType": "query",
            }
        elif _pref.get("approvals"):
            # 审批：不执行工具（不发出 tool_start），交给 _apply_write_gates 弹确认
            tool_result = {
                "tool": routing_result.tool_name,
                "arguments": params,
                "result": "需要审批",
                "rowCount": 0, "source": "rule_engine", "needs_approval": True,
                "approvals": _pref["approvals"], "actionType": "query",
            }
        else:
            # 通过：发出 tool_start 并执行
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
                    routing_result.tool_name, params, user_id=user_id, preflight=_pref,
                )
        # 写操作留痕：记录落点 + 可回滚性（为回滚/审计打基础）
        _tool_at = tool_result.get("actionType", "query")
        _tool_landing = {}
        if _tool_at in ("create", "delete", "update", "write"):
            _lc = sig.get("conceptName", "")
            if _lc:
                try:
                    from app.services.multi_system_backend import multi_system_backend
                    _tool_landing = multi_system_backend.get_write_landing(_lc)
                except Exception:
                    _tool_landing = {}
        # 查询结果完整数据（展示值）传给前端做抽屉分页；截断到安全上限避免 SSE/DB 膨胀
        _records_all = tool_result.get("records", []) or []
        _records_cap = _records_all[:200]
        yield ('tool_result', _json.dumps({
            "tool": routing_result.tool_name,
            "label": sig.get("actionLabel", "") or sig.get("conceptLabel", ""),
            "rowCount": tool_result.get("rowCount", 0),
            "source": tool_result.get("source", ""),
            "sourceLabel": tool_result.get("sourceLabel", ""),
            "actionType": _tool_at,
            "landing": _tool_landing,
            "reversible": _tool_landing.get("reversible", True),
            "before_snapshot": tool_result.get("before_snapshot", []),
            "records": _records_cap,
            "columns": tool_result.get("columns", []) or [],
        }))

        # ── 查询 0 条反思兜底 ──
        # 单工具直查（L2 高置信度命中）不经过 dynamic planner 的反思，
        # DSH 对齐：查询 0 条不盲目「去参数全量重查」、也不做正则「反思」——那是确定性兜底，
        # 会掩盖参数填错、返回无关全量数据；DSH 是 LLM 看到 0 条结果自己反思（理解归 LLM）。
        # 这里保留 _query_via_backend 的空结果诊断（含样本对比分析），由 react loop 把 0 条结果
        # 喂回下一轮 LLM 决策（LLM 决定调整参数重查，或诚实告知「没有找到」）。
        _is_query = tool_result.get("actionType") == "query" or routing_result.tool_name.endswith("_query")
        if _is_query and tool_result.get("rowCount", 0) == 0:
            log.warning(f"[{self.name}] REFLECT-CHECK is_query={_is_query} rowCount={tool_result.get('rowCount',0)} fuzzy={params.get('_fuzzy')!r} params_keys={list(params.keys())}")
            # 0 条：保留 _query_via_backend 的空结果诊断（tool_result["result"] 已是含样本对比的诊断文本），
            # 不覆盖、不重查（不再做确定性去参数重查 / 正则反思——对齐 DSH，由 react loop 把 0 条结果
            # 喂回 LLM 反思，LLM 自己决定调整参数重查或诚实告知「没有找到」）。
            pass
        out["tool_result"] = tool_result
        out["tool_landing"] = _tool_landing



    async def _apply_write_gates(
        self, *, routing_result, params, tool_result, tool_landing,
        session_id, user_id, out,
    ) -> AsyncGenerator[tuple, None]:
        """预警 + 规则审批 + 违规修正 + 推理确认，写 out['tool_result']；拦截则提前 return。"""
        import json as _json
        from app.services.intent_router import intent_router
        from app.services.action_executor import action_executor

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
                # 统一走「待审批」列表（含有审批权限的用户与管理员）：
                # 审批是留痕行为，有权限的人确认 = 审批动作，应在待审批里做
                #（与无权限委托路径一致，自审自批也有审计记录）；
                # 管理员超管看得到全部待办（messages 列表放开），不会再死锁。
                assigned = list(approval_roles)
                _schema = await intent_router.get_param_schema(routing_result.tool_name)
                _irreversible_tool = bool(tool_landing.get("is_api") and not tool_landing.get("reversible"))
                _pack = self._build_decision_pack(params, {}, _schema, irreversible=_irreversible_tool)
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
                yield ('content', f"操作已确认，规则「{rule_labels}」触发审批。已提交待办，请到「待审批」菜单确认后执行。")
            else:
                yield ('content', f"操作被规则「{rule_labels}」拦截（规则未配置审批角色，无法提交审批）。")
            yield ('execution_done', _json.dumps({
                "totalSteps": 4, "cancelled": True,
                "delegated": True if approval_roles else None,
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
            approved, retry_params = await self._wait_for_confirmation(
                session_id, timeout=None, event=self._prepare_confirmation(session_id, params=params))
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
            inf_confirm_event = self._prepare_confirmation(session_id, params=params)
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
        # ── 写操作 SOP：执行后回读复查（对齐 DSH「删后复查确认生效」）──
        _at = tool_result.get("actionType", "")
        if _at in ("create", "update", "delete") and tool_result.get("source") != "rule_engine" \
                and (tool_result.get("rowCount", 0) or 0) > 0:
            try:
                from app.services.write_sop import postcheck as _sop_postcheck
                _sig_pc = action_executor._sigs.get(routing_result.tool_name, {})
                _concept_pc = _sig_pc.get("conceptName", "")
                if _at == "create":
                    _pk_name = next((k for k in ("code", "id") if params.get(k)), "code")
                    _pk_val = tool_result.get("created_entity_id")
                else:
                    _pk_name = next((k for k in ("code", "id") if params.get(k)), "")
                    _pk_val = next((params.get(k) for k in ("code", "id") if params.get(k)), None)
                if _pk_name and _pk_val:
                    _ck = await _sop_postcheck(_concept_pc, _pk_name, _pk_val, _at)
                    if _ck:
                        yield ('content', f"\n\n{_ck}\n")
            except Exception as _pe2:
                log.warning(f"[{self.name}] 写操作复查回读异常: {_pe2}")

        out["tool_result"] = tool_result



    async def _format_result(
        self, *, routing_result, tool_result, message, session_id, model_name, params=None,
    ) -> AsyncGenerator[tuple, None]:
        """LLM 格式化查询/操作结果并流式输出。"""
        import json as _json
        from app.core.tracing import span
        from app.services.llm_service import llm_service

        # ── LLM format only ──
        yield ('format_start', _json.dumps({}))

        from app.core.prompts import FORMAT_ONLY_SYSTEM_PROMPT, TABLE_COLUMN_RULE
        tool_result_text = tool_result.get("result", "")
        # 关键字段确定性注入：优先用显式传入 params，回退 tool_result._params
        params = params or tool_result.get("_params") or {}

        # 根据操作类型生成不同的格式化指令
        _action_type = tool_result.get("actionType", "query")
        _row_count = tool_result.get("rowCount", 0)
        # 关键字段确定性注入（写操作）：从实际执行参数取，LLM 只能复述不得编造
        # （此前 format 让 LLM 自由发挥字段，出现过「物料 M-2023-B12/150件/SMT车间」与实际参数不符的幻觉）
        _key_fields = ""
        if _action_type in ("create", "update", "delete"):
            try:
                from app.services.action_executor import action_executor as _aex_fmt
                _sig_fmt = _aex_fmt._sigs.get(routing_result.tool_name, {}) or {}
                _lab_map = {p.get("name"): (p.get("label") or p.get("name"))
                            for p in _sig_fmt.get("params", [])}
                _parts = []
                if _action_type == "delete":
                    # 删除：关键字段来自删前快照（被删记录的完整字段），用户只给了主键
                    _snap = tool_result.get("before_snapshot") or []
                    _rec = _snap[0] if _snap and isinstance(_snap[0], dict) else {}
                    for _k, _lbl in _lab_map.items():
                        if str(_k).startswith("_") or _k not in _rec:
                            continue
                        _parts.append(f"{_lbl}={_rec.get(_k)}")
                else:
                    # create/update：关键字段来自实际执行参数（用户确认过的值）
                    for _k, _v in (params or {}).items():
                        if str(_k).startswith("_"):
                            continue
                        _parts.append(f"{_lab_map.get(_k, _k)}={_v}")
                _key_fields = "、".join(_parts)
            except Exception:
                _key_fields = ""
        _key_block = f"### 本次操作的关键字段（只能复述这些值，不得编造或替换）\n{_key_fields}\n\n" if _key_fields else ""
        if _action_type == "delete":
            format_message = (
                f"### 操作结果\n{tool_result_text}\n\n"
                f"{_key_block}"
                f"### 用户消息\n{message}\n\n"
                f"请用简洁中文报告删除结果，包含：①删除成功（或失败）的确认 ②关键字段（严格用上面给的值）"
                f"③一句收尾（如「如需恢复或继续其他操作请告诉我」）。"
                f"不要表格，不要重复查证过程（查证结论已在上文展示）。"
            )
        elif _action_type == "create":
            format_message = (
                f"### 操作结果\n{tool_result_text}\n\n"
                f"{_key_block}"
                f"### 用户消息\n{message}\n\n"
                f"请用简洁中文报告创建结果，包含：①创建成功（或失败）的确认 ②关键字段（严格用上面给的值，"
                f"新工单号从操作结果里取真实值）③一句收尾（如「可前往工单列表查看详情」）。"
                f"不要表格，不要重复查证过程。"
            )
        elif _action_type == "update":
            format_message = (
                f"### 操作结果\n{tool_result_text}\n\n"
                f"{_key_block}"
                f"### 用户消息\n{message}\n\n"
                f"请用简洁中文报告更新结果，包含：①更新成功（或失败）的确认 ②关键字段（严格用上面给的值）"
                f"③一句收尾建议。不要表格，不要重复查证过程。"
            )
        elif _row_count == 0 or "未找到" in tool_result_text:
            # 空结果：区分「带诊断样本」vs「纯无数据」。
            # 带样本时让 LLM 对比条件与样本做具体原因分析（理解归 LLM），
            # 而不是笼统一句「没有找到」。
            if "样本" in tool_result_text or "抽查" in tool_result_text:
                format_message = (
                    f"### 查询结果（未匹配，附诊断样本）\n{tool_result_text}\n\n"
                    f"### 用户消息\n{message}\n\n"
                    f"请基于上面的样本，具体分析为什么用户的查询条件没有匹配到记录，"
                    f"并给出可操作建议。要说明原因，不要笼统说「没有找到」。"
                )
            else:
                format_message = (
                    f"### 查询结果\n{tool_result_text}\n\n"
                    f"### 用户消息\n{message}\n\n"
                    f"查询无结果，请直接一句话告知用户没有匹配数据，不要输出表格。"
                )
        else:
            format_message = (
                f"### 查询结果\n{tool_result_text}\n\n"
                f"### 用户消息\n{message}\n\n"
                f"请基于以上查询结果回复用户消息。{TABLE_COLUMN_RULE}。"
                f"回复末尾可用 1~2 句点出值得注意的洞察（如异常状态、明显规律、可跟进事项），不要编造。"
            )

        system_prompt = await self.build_system_prompt(include_tools_prompt=False, user_message=message)
        system_prompt = f"{FORMAT_ONLY_SYSTEM_PROMPT}\n\n{system_prompt}"

        # 格式化回复跟随对话模型（统一），decision_model 仅兜底
        from app.agents.settings.model import MODEL_CONFIG
        async with span("format", "generic"):
            async for t, c in llm_service.chat_stream(
                message=format_message, session_id=session_id,
                system_prompt=system_prompt,
                model_name=model_name or MODEL_CONFIG.get("decision_model"),
                use_agent=False, web_search=False,
                history_messages=None,  # 格式化只需查询结果+当前消息，历史里的负面文本会污染判断
                enable_thinking=False,
                tools=None,  # NO tools — format only
            ):
                yield t, c

        yield ('execution_done', _json.dumps({
            "method": routing_result.method,
            "tool": routing_result.tool_name,
        }))






    async def _execute_multi_step(
        self, *, message, original_message, session_id, model_name,
        enable_thinking, history_messages, user_id, _is_ask_followup,
        candidate_list, onto_tools, concept_names, _track, _t_start,
    ) -> AsyncGenerator[tuple, None]:
        """执行 multi_step 决定：动态规划多步，失败则兜底澄清/LLM 兜底。"""
        import json as _json
        import time as _t
        from app.core.config import settings

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
