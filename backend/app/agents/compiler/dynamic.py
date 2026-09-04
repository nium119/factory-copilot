"""动态多跳查询规划器 — ReAct 风格 LLM 决策。

查询统一走 action executor，不做二次降级。
"""
import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import AsyncGenerator, Optional

from loguru import logger

from app.core.tracing import span


def _get_configured_model(key: str) -> str:
    """从全局配置读取模型"""
    from app.agents.settings.model import MODEL_CONFIG
    return MODEL_CONFIG.get(key)


# ── Skill 运行时模型 ────────────────────────────────────────


@dataclass
class SkillField:
    name: str = ""
    label: str = ""
    type: str = "string"


@dataclass
class SkillParam:
    name: str = ""
    label: str = ""
    type: str = "string"
    required: bool = False


@dataclass
class DataSource:
    type: str = "neo4j"
    connection: str = ""


@dataclass
class AtomicSkill:
    name: str = ""
    display_name: str = ""
    concept: str = ""
    concept_label: str = ""
    description: str = ""
    triggers: list = field(default_factory=list)
    input_params: list = field(default_factory=list)
    output_fields: list = field(default_factory=list)
    data_source: Optional[DataSource] = None
    actions: list = field(default_factory=list)


@dataclass
class CompositeSkill:
    name: str = ""
    display_name: str = ""
    description: str = ""
    path: list = field(default_factory=list)
    steps: list = field(default_factory=list)
    triggers: list = field(default_factory=list)
    source: str = "discovered"


@dataclass
class AgentDefinition:
    name: str = ""
    display_name: str = ""
    icon: str = "🤖"
    color: str = "#6c5ce7"
    description: str = ""
    project_description: str = ""
    system_prompt: str = ""
    skill_names: list = field(default_factory=list)
    chain_names: list = field(default_factory=list)


@dataclass
class CompiledRuntime:
    skills: list = field(default_factory=list)
    chains: list = field(default_factory=list)
    agents: list = field(default_factory=list)
    skill_catalog_text: str = ""
    relation_graph_text: str = ""
    compiled_at: str = ""
    concept_count: int = 0

    @property
    def chains_display(self) -> list:
        result = []
        for c in self.chains:
            result.append({
                "name": c.name,
                "display_name": c.display_name,
                "path": c.path,
            })
        return result


# ── DynamicPlanner ───────────────────────────────────────────


class DynamicPlanner:
    """ReAct 风格的动态多跳查询规划器。"""

    MAX_STEPS = 6  # 兜底默认；实际值由 RESOURCE_THRESHOLDS.planner_max_steps 覆盖（前端「资源阈值」可调）

    def __init__(self, runtime: CompiledRuntime):
        self.runtime = runtime
        self._skill_map = {s.name: s for s in runtime.skills}
        self._concept_skill_map = {s.concept: s for s in runtime.skills}
        self._mcp_tools: dict = {}  # P3：外部 MCP 工具 {name: sig}，loop 可自主调度

        # 可靠性预算（单次分析会话）：从「资源阈值」读取，前端可调，改动后下次对话生效
        from app.agents.settings.resource import RESOURCE_THRESHOLDS
        _th = RESOURCE_THRESHOLDS
        self.MAX_STEPS = int(_th.get("planner_max_steps", 6) or 6)
        self._time_budget_s = float(_th.get("planner_time_budget_s", 60) or 60)
        self._max_llm_calls = int(_th.get("planner_max_llm_calls", 12) or 12)
        self._summary_max_chars = int(_th.get("planner_summary_max_chars", 1500) or 1500)
        # 单次执行状态：预算计时 / LLM 调用计数 / 已查询概念计数（循环检测）
        self._t0 = time.time()
        self._llm_calls = 0
        self._queried: dict = {}
        # 递归展开状态（本体 traversal=recursive 驱动的多层下钻）：
        # _expanded 记录已展开的 (概念, target, join值) 防死循环；_rec_added 递归步计数（单独限额）
        self._expanded: set = set()
        self._rec_added = 0

    def build_planner_prompt(self) -> str:
        """构建注入给 LLM 的规划上下文。"""
        parts = [
            "你是制造业智能助手。你可以：① 查询以下概念的数据；② 执行写操作（创建/更新/删除/排程/插单）。"
            "用户要求写操作（如「创建工单」「排程」「插单」）时，必须把对应写操作作为步骤规划进去（type=action），不要只做查询分析。",
            "",
            self.runtime.skill_catalog_text,
            "",
        ]

        # 注入本体关系图：LLM 依据概念间的关系推理多跳分析路径，
        # 避免只凭关键词命中错误概念（如"换型"误判到 WorkOrderTask）
        if self.runtime.relation_graph_text:
            parts.append(self.runtime.relation_graph_text)
            parts.append("")

        # P3：注入外部 MCP 工具（可被 loop 自主调度；写操作走统一治理需审批）
        if self._mcp_tools:
            mcp_lines = ["## 外部 MCP 工具", ""]
            for name, sig in self._mcp_tools.items():
                desc = (sig.get("description", "") or "")[:120]
                mcp_lines.append(f"- {name}: {desc}")
            mcp_lines.append("")
            mcp_lines.append("（MCP 只读工具可直接调用；写操作需审批确认）")
            parts.append("\n".join(mcp_lines))
            parts.append("")

        if self.runtime.chains:
            parts.append("## 预定义分析路径 (优先使用)")
            for c in self.runtime.chains:
                parts.append(f"- {c.display_name}: {' → '.join(c.path)}")
            parts.append("")

        # 注入可执行写操作（本体动作），让规划器能拆解「创建工单并排程」这类复合任务
        try:
            from app.services.ontology_service import ontology_service as _os2
            _sigs = _os2.get_action_signatures() or []
            _write_lines = ["## 可执行写操作（用户要求创建/更新/删除/排程/插单时，用动作名规划）", ""]
            _seen_w = set()
            _wcount = 0
            for _s in _sigs:
                _fn = _s.get('functionName') or ''
                if not _fn or _fn.endswith('_query') or _fn.startswith('mcp_'):
                    continue
                _k = (_s.get('conceptLabel') or '', _s.get('actionLabel') or '')
                if _k in _seen_w:
                    continue
                _seen_w.add(_k)
                _write_lines.append(f"- {_fn}：{_s.get('actionLabel') or _fn}（{_s.get('conceptLabel') or ''}）")
                # 参数 schema：让 LLM 用本体字段名规划 params（而非中文标签，避免字段不匹配导致写失败）
                _params = _s.get('params') or []
                if _params:
                    _pstr = "、".join(
                        f"{p.get('name')}({p.get('label') or p.get('name')})" for p in _params[:8]
                    )
                    _write_lines.append(f"    参数: {_pstr}")
                _wcount += 1
            if _wcount:
                _write_lines.append("")
                parts.append("\n".join(_write_lines))
        except Exception:
            pass

        parts.append("## 分析规则")
        parts.append("1. 一次只查询一个概念")
        parts.append(f"2. 根据查询结果中的关联数据决定下一步，最多 {self.MAX_STEPS} 步")
        parts.append("3. 查询或写操作完成后输出汇总结论 + P0/P1/P2 行动项")
        parts.append("4. 无数据时如实告知，不编造")
        parts.append("5. 单维度不确定（如仅缺时间）→ 用默认值（如本月）。多维度不确定（缺概念+缺时间）→ ASK分组确认。")
        parts.append("6. 当前消息简短且有对话历史时，是追问回复，提取历史中的完整意图直接执行，不要再次反问。")
        parts.append("7. 始终先查用户直接指定的概念（如工单），再查关联概念。用上一跳结果的ID/编号值做过滤。例如：先查WorkOrder获取id=990，再查WorkOrderBOM带上workOrderCode=990。禁止无过滤条件查全表。")
        parts.append("8. 影响/取消/延期类分析：沿「概念关系图」从事件源头概念出发，向关联概念扩散追查（如订单→分录→物料→库存/采购/到货），不要只查事件本身。")
        parts.append("9. BOM/结构展开：用户问某物料的 BOM/子件/组件/由什么构成时，必须先查该物料本身（用其编码/ID 定位），再沿它的「递归」关系逐层下钻得到子件物料（可多级）。禁止把物料编码直接当成 BOM 节点/清单项的主键去查——那些是展开过程的中间载体，最终要回答的是子件物料及其库存/采购/在途。")
        parts.append("")
        parts.append("## 相似匹配规则（仅用户明确要求匹配相似/找相似时生效）")
        parts.append("- 用户要求「匹配相似X」或「找相似X」时，第一步直接使用 FIND_SIMILAR 工具，不要先做常规查询")
        parts.append("- 格式: FIND_SIMILAR: 概念名 target=目标标识 reason=简述")
        parts.append("- 示例: FIND_SIMILAR: WorkOrderBOM target=MO001 reason=用户想找相似BOM")
        parts.append("")
        parts.append("## 输出格式")
        parts.append("如果有歧义或信息不足，先反问: ASK: <需要确认的问题>")
        parts.append("如果需要查询，回复: QUERY: 概念名 原因简述")
        parts.append("如果需要相似匹配，回复: FIND_SIMILAR: 概念名 target=目标标识 reason=简述")
        parts.append("如果可以总结，回复: SUMMARY: 汇总内容")
        parts.append("（注意：概念名只能是一个，不要加括号或其他字符）")

        return "\n".join(parts)

    async def _plan_steps(self, message: str, history_messages: list) -> tuple:
        """Phase 1 计划：LLM 一次输出完整的多步查询步骤序列（先计划后执行）。

        相比逐步骤 LLM 决策：计划一次定死、执行确定性，避免每步随机选概念、
        提前汇总导致链路不完整。返回 (steps, ask)：
        - steps: [{"concept": ..., "reason": ..., "type": "query"|"find_similar"}]
        - ask: 需澄清的问题（无则 None）
        """
        try:
            from app.services.llm_service import llm_service
            planner = self.build_planner_prompt()
            plan_instruction = (
                "\n## 本次任务输出格式（只输出 JSON，不要其他文字）\n"
                '{"steps": [{"concept": "概念名", "reason": "理由", "type": "query|find_similar|action", "action": "动作名", "params": {}}, ...], "ask": null, "options": null}\n'
                "规则：\n"
                f"- 根据用户消息一次规划完整的多步步骤序列，最多 {self.MAX_STEPS} 步\n"
                "- 查询步骤：concept 填概念名（来自上面可查询的概念）；用上一跳结果值过滤下一跳\n"
                '- 相似匹配：type="find_similar" + target="目标标识"\n'
                '- 写操作步骤：type="action" + action="动作名"（来自上面可执行写操作，如 WorkOrder_create、WorkOrder_schedule）+ params=参数对象；用户要求创建/更新/删除/排程/插单时，把对应动作作为步骤规划进去\n'
                "- 用户消息已含明确对象/编码（如 ECN2026-002、MO001）或明确分析意图（变更/影响/分析/库存/工单）时，必须直接规划，禁止 ask\n"
                '- 仅当消息完全没有业务对象和意图时才输出 ask：{"steps": [], "ask": "需要确认的问题"}\n'
                '- 输出 ask 时，可同时输出 options 给用户点选（2-4 个），每个含 label（简短选项名）和 description（一句话说明该选项的含义）；推荐的选项放第一个并加 "recommended": true；选项确实能覆盖用户可能的意图时才输出，否则 options 留 null\n'
                '- 写操作缺多个必填参数需要逐项收集时，改输出 groups 数组（前端渲染成逐题问卷）：'
                '每组 {"label": "字段中文名（问法）", "options": [{label, description}...或空数组], "required": true|false}；'
                'required 按该参数是否为本体 action 的必填项如实标注（必填 true、可选 false），不要全标 true'
            )
            prompt = planner + plan_instruction + f"\n## 用户消息\n{message}"
            model = _get_configured_model("decision_model")
            self._llm_calls += 1  # 预算计数：计划
            async with span("dynamic_plan", "generic"):
                raw = await asyncio.wait_for(
                    llm_service.chat_sync(
                        message=prompt,
                        system_prompt="你是多步分析规划器，只输出 JSON，不输出任何解释。",
                        model_name=model,
                    ),
                    timeout=15.0,
                )
            raw = raw.strip()
            if raw.startswith("```"):
                lines = raw.split("\n")
                raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:]).strip()
            parsed = json.loads(raw)
            ask = parsed.get("ask") or None
            # 结构化追问选项：归一化（只保留有 label 的对象），供前端渲染成点选卡片
            _opts = []
            for _o in parsed.get("options") or []:
                if isinstance(_o, dict) and _o.get("label"):
                    _opt = {"label": str(_o["label"])}
                    if _o.get("description"):
                        _opt["description"] = str(_o["description"])
                    if _o.get("recommended"):
                        _opt["recommended"] = True
                    _opts.append(_opt)
            ask_options = _opts
            # 确定性兜底：LLM 给了 ask 但没给 options → 用可查询概念生成点选选项，
            # 避免退回纯文本反问（用户只能打字）
            if ask and not ask_options:
                _seen_lb, _gen_opts = set(), []
                for _sk in self.runtime.skills:
                    _lb = (getattr(_sk, "concept_label", "") or "").strip()
                    if not _lb or _lb in _seen_lb:
                        continue
                    _seen_lb.add(_lb)
                    _gen_opts.append({"label": _lb, "description": f"查询{_lb}相关信息"})
                    if len(_gen_opts) >= 6:
                        break
                if _gen_opts:
                    ask_options = _gen_opts
            # 概念 label → 概念名 映射（LLM 规划/评审可能输出中文 label，如"工程变更通知"→ECN）
            concept_label_map = {}
            for _sk in self._concept_skill_map.values():
                concept_label_map[_sk.concept] = _sk.concept
                if getattr(_sk, 'concept_label', None):
                    concept_label_map[_sk.concept_label] = _sk.concept
            # P3：MCP 工具名可直接被规划选择
            for _mcp in self._mcp_tools:
                concept_label_map[_mcp] = _mcp

            steps = []
            for s in parsed.get("steps", []) or []:
                if not isinstance(s, dict):
                    continue
                _stype = str(s.get("type", "")).strip()
                # 写操作步骤：type="action" + action + params，保留动作名/参数（规划器支持写操作）
                if _stype == "action":
                    _action = str(s.get("action", "")).strip()
                    if _action:
                        steps.append({
                            "concept": _action,
                            "reason": str(s.get("reason", ""))[:80],
                            "type": "action",
                            "action": _action,
                            "params": s.get("params", {}) or {},
                        })
                    continue
                concept = concept_label_map.get(str(s.get("concept", "")).strip())
                if concept and (concept in self._concept_skill_map or concept in self._mcp_tools):
                    steps.append({
                        "concept": concept,
                        "reason": str(s.get("reason", ""))[:80],
                        "type": "find_similar" if _stype == "find_similar" else "query",
                        "target": str(s.get("target", "")).strip(),
                    })
            # 需求覆盖评审（LLM 语义，无硬编码映射）：看计划是否覆盖用户需求，缺失则补
            steps = await self._review_plan(message, steps)
            if steps:
                logger.info(f"[DynamicPlanner] 计划 {len(steps)} 步: {[s['concept'] for s in steps]}")
            return steps, ask, ask_options

        except asyncio.TimeoutError:
            logger.warning("[DynamicPlanner] 计划超时，回退无计划")
        except Exception as e:
            logger.warning(f"[DynamicPlanner] 计划失败: {e}")
        return [], None, []

    async def _review_plan(self, message: str, steps: list) -> list:
        """需求覆盖评审（LLM 语义，无硬编码映射）：判断计划是否覆盖用户需求，缺失概念则补。

        输入消息 + 当前计划 + 可查询概念目录（build_planner_prompt 含 skill 目录 + 本体关系图），
        LLM 判断计划缺失的需求概念并补充——如"分析 ECN 对库存、生产影响"缺库存/工单概念则补。
        失败返回原 steps（不影响执行）。
        """
        if not steps:
            return steps
        # 确定性影响链路扩展（通用，不写死概念名）：沿本体关系图扩散，再交给 LLM 评审补充
        steps = self._expand_impact_chain(message, steps)
        # 确定性 BFS 重排：确保执行顺序遵循关系依赖（BOM头→BOM分录→库存/采购），不受 LLM 随机顺序影响
        steps = self._reorder_by_relation_bfs(steps)
        # BOM 展开确定性下钻：BOM 分录后补「子件物料主数据」，让报告能显示子件名称
        steps = self._insert_child_material(steps)
        try:
            from app.services.llm_service import llm_service
            planner = self.build_planner_prompt()  # 含可查询概念目录 + 关系图
            plan_text = "\n".join(f"- {s['concept']}：{s.get('reason', '')}" for s in steps)
            prompt = planner + "\n\n" + (
                "## 评审任务\n"
                "判断下面的多步查询计划是否完整覆盖用户需求。\n\n"
                f"用户消息：{message}\n\n"
                f"当前计划步骤：\n{plan_text}\n\n"
                "若计划缺失用户需求相关的概念，从上面可查询概念中补充"
                "（如用户问'影响库存/生产'但计划没查库存/工单相关概念，则补充）。\n"
                '输出 JSON：{"add": [{"concept": "概念名", "reason": "理由"}]}，无需补充则 {"add": []}\n'
                "只输出 JSON，不要其他文字。"
            )
            model = _get_configured_model("decision_model")
            self._llm_calls += 1  # 预算计数：计划评审
            async with span("dynamic_review", "generic"):
                raw = await asyncio.wait_for(
                    llm_service.chat_sync(
                        message=prompt,
                        system_prompt="你是分析计划评审器，只输出 JSON。",
                        model_name=model,
                    ),
                    timeout=10.0,
                )
            raw = raw.strip()
            if raw.startswith("```"):
                lines = raw.split("\n")
                raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:]).strip()
            parsed = json.loads(raw)
            # 概念 label → 概念名 归一（LLM 可能输出中文 label）
            concept_label_map = {}
            for _sk in self._concept_skill_map.values():
                concept_label_map[_sk.concept] = _sk.concept
                if getattr(_sk, 'concept_label', None):
                    concept_label_map[_sk.concept_label] = _sk.concept
            planned = {s['concept'] for s in steps}
            for a in parsed.get("add", []) or []:
                if not isinstance(a, dict):
                    continue
                c = concept_label_map.get(str(a.get("concept", "")).strip())
                if c and c in self._concept_skill_map and c not in planned:
                    steps.append({
                        "concept": c,
                        "reason": str(a.get("reason", ""))[:60],
                        "type": "query",
                    })
                    planned.add(c)
            if steps:
                logger.warning(f"[DynamicPlanner] 计划评审后步骤: {[s['concept'] for s in steps]}")
            return steps
        except Exception as e:
            logger.warning(f"[DynamicPlanner] 计划评审失败，保留原计划: {e}")
            return steps

    def _expand_impact_chain(self, message: str, steps: list) -> list:
        """确定性影响链路扩展（委托 GraphEngine，通用，不写死概念名）。

        影响/取消/延期意图 = 沿本体关系从源头 BFS 2 跳扩散。逻辑收编到 GraphEngine，
        作为执行层确定性工具（阶段 C Graph-Loop 融合）。
        """
        from app.agents.graph_engine import graph_engine
        planned = [s['concept'] for s in steps]
        added = graph_engine.expand_impact(planned, message)
        if added:
            steps = steps + added
            logger.info(f"[DynamicPlanner] 影响链路扩展补 {len(added)} 概念: {[a['concept'] for a in added]}")
        return steps

    def _reorder_by_relation_bfs(self, steps: list) -> list:
        """按本体关系做确定性 BFS 排序（委托 GraphEngine，通用，不写死概念名）。

        逻辑收编到 GraphEngine.reorder_bfs，作为执行层确定性工具（阶段 C Graph-Loop 融合）。
        """
        from app.agents.graph_engine import graph_engine
        return graph_engine.reorder_bfs(steps)

    def _insert_child_material(self, steps: list) -> list:
        """递归关系下钻（委托 GraphEngine，本体 traversal 驱动，不写死概念名）。"""
        from app.agents.graph_engine import graph_engine
        return graph_engine.insert_child_material(steps)

    def _recursive_pending(self, concept: str, records: list, depth: int) -> list:
        """执行时递归展开（委托 GraphEngine，本体 traversal=recursive 驱动）。

        self._expanded 作为去重状态传给 GraphEngine，防跨对话污染。
        """
        from app.agents.graph_engine import graph_engine
        return graph_engine.recursive_pending(concept, records, depth, self._expanded)

    async def execute(
        self,
        message: str,
        model_name: Optional[str] = None,
        enable_thinking: Optional[bool] = None,
        session_id: str = "",
        history_messages: list = None,
    ) -> AsyncGenerator[tuple, None]:
        """动态执行多跳查询。"""
        from app.services.action_executor import action_executor
        # P3：加载外部 MCP 工具，纳入 loop 自主调度（写操作走 P0 统一治理）
        self._mcp_tools = {
            name: sig for name, sig in action_executor._sigs.items()
            if sig.get("source") == "mcp"
        }

        context = {"message": message}
        steps_taken = []
        # 重置单次执行预算状态（时间 / LLM 调用 / 循环检测计数 / 递归展开状态）
        self._t0 = time.time()
        self._llm_calls = 0
        self._queried = {}
        self._expanded = set()
        self._rec_added = 0

        # Phase 1: 计划——一次 LLM 输出完整步骤序列（先计划后执行，业界标准）。
        # 相比逐步骤 LLM 决策：计划一次定死、执行确定性，根治"每步随机/提前汇总"。
        # 规划是非流式 LLM（可能数秒），先发「规划中」步骤，前端立即显示链、不干等
        yield ('step', json.dumps({
            "step": 0, "action": "plan_start",
            "description": "正在规划分析步骤…",
        }, ensure_ascii=False))
        steps, ask, ask_options = await self._plan_steps(message, history_messages)
        # 规划完成：把「规划中」步骤标记为 done
        yield ('step', json.dumps({
            "step": 0, "action": "plan_done",
            "description": "分析步骤规划完成",
        }, ensure_ascii=False))
        if ask:
            yield ('content', f"\n\n---\n### 需要确认\n\n{ask}")
            _done = {"steps_taken": 0}
            if ask_options:
                _done["quick_replies"] = ask_options
            yield ('done', json.dumps(_done, ensure_ascii=False))
            return
        if not steps:
            yield ('error', "无法规划分析步骤，请补充信息")
            yield ('done', json.dumps({"steps_taken": 0, "max_steps": self.MAX_STEPS}))
            return

        summary_produced = False
        # 主循环用可变步骤列表 + 索引遍历：执行中沿本体 traversal=recursive 关系
        # 动态插入递归展开步骤（多层 BOM 下钻），for 循环无法在遍历中插入
        _steps_live = list(steps)
        _exec_idx = 0
        while _exec_idx < len(_steps_live):
            step = _steps_live[_exec_idx]
            step_num = _exec_idx + 1
            _exec_idx += 1
            concept = step.get("concept", "")
            reason = step.get("reason", "")

            # 预算硬上限：步骤数 / 执行时间 / LLM 调用超限 → 停止新查询，基于已有数据强制汇总
            _exhausted, _budget_reason = self._budget_exhausted(max(0, len(steps_taken) - self._rec_added))
            if _exhausted:
                logger.warning(f"[DynamicPlanner] 预算限制（{_budget_reason}），强制汇总（已执行 {len(steps_taken)} 步）")
                yield ('think', json.dumps({
                    "step": step_num, "concept": "", "concept_label": "预算限制",
                    "content": f"预算限制：{_budget_reason}，停止继续查询，基于已有数据汇总",
                }, ensure_ascii=False))
                summary_produced = True
                # 影响判定（确定性计算，预算限制强制汇总前也要注入，确保报告包含）
                try:
                    from app.services.impact_judger import judge_impact
                    _ij = await judge_impact(context, steps_taken, message)
                    if _ij:
                        context["_impact_judgement"] = _ij
                        steps_taken.append({
                            "step": len(steps_taken) + 1,
                            "concept": "_impact_judgement",
                            "label": "影响判定",
                            "result": _ij,
                        })
                except Exception as _ije:
                    logger.warning(f"[DynamicPlanner] 影响判定失败: {_ije}")
                yield ('step', json.dumps({
                    "step": step_num, "action": "summary",
                    "description": "综合汇总（预算限制）",
                    "model": model_name or _get_configured_model("summary_model"),
                }, ensure_ascii=False))
                yield ('content', "\n\n---\n### 综合汇总\n\n")
                async for chunk_type, chunk_content in self._llm_summarize(
                    self._build_decision_prompt(
                        self.build_planner_prompt(), message, steps_taken, context, step_num, history_messages,
                    ), context, model_name, enable_thinking, session_id, steps_taken,
                ):
                    yield (chunk_type, chunk_content)
                # 影响判定结论固定输出（确定性，不依赖 LLM 引用，避免被汇总省略）
                _ij_fixed = (context or {}).get("_impact_judgement", "")
                if _ij_fixed:
                    yield ('content', f"\n\n---\n### 影响判定结论\n\n{_ij_fixed}\n")
                break
            # 计划步骤类型：find_similar（相似匹配）/ action（写操作）/ query（默认查询）
            _step_type = step.get("type") or "query"
            if _step_type == "action":
                decision = {
                    "action": "write_action",
                    "action_name": step.get("action", ""),
                    "params": step.get("params", {}) or {},
                    "reason": reason,
                    "concept": concept,
                }
            else:
                decision = {
                    "action": "find_similar" if _step_type == "find_similar" else "query",
                    "concept": concept,
                    "reason": reason,
                    "target": step.get("target", ""),
                    "targetKey": step.get("target", ""),
                }

            if decision["action"] == "ask":
                reason = decision.get("reason", "")
                groups = decision.get("groups", [])
                yield ('content', f"\n\n---\n### 需要确认\n\n{reason}")
                done_data = {"steps_taken": len(steps_taken)}
                if groups:
                    done_data["quick_replies"] = groups
                yield ('done', json.dumps(done_data))
                return

            if decision["action"] == "summary":
                summary_produced = True
                yield ('step', json.dumps({
                    "step": step_num, "action": "summary",
                    "description": "综合汇总",
                    "model": model_name or _get_configured_model("summary_model"),
                }, ensure_ascii=False))
                yield ('content', "\n\n---\n### 综合汇总\n\n")
                async for chunk_type, chunk_content in self._llm_summarize(
                    self._build_decision_prompt(
                        self.build_planner_prompt(), message, steps_taken, context, step_num, history_messages,
                    ), context, model_name, enable_thinking, session_id, steps_taken,
                ):
                    yield (chunk_type, chunk_content)
                break

            elif decision["action"] == "write_action":
                # 任务规划器产出的写操作步骤：调用 action_executor 执行（排程/创建/更新等）
                _action_name = decision.get("action_name", "")
                _params = decision.get("params", {}) or {}
                _reason = decision.get("reason", "")
                # 参数对齐：LLM 可能用中文标签（如"工单号"）作 key，映射到本体字段名（如 code），
                # 避免字段不匹配导致写操作落空。
                if _params and _action_name:
                    try:
                        from app.services.action_executor import action_executor as _ae_align
                        _sig = _ae_align._sigs.get(_action_name, {}) or {}
                        _label2name = {
                            str(p.get("label")): p.get("name")
                            for p in (_sig.get("params") or [])
                            if p.get("label") and p.get("name") and str(p.get("label")) != p.get("name")
                        }
                        if _label2name:
                            _aligned = {}
                            for _k, _v in _params.items():
                                _aligned[_label2name.get(str(_k), _k)] = _v
                            _params = _aligned
                    except Exception:
                        pass
                # 写操作必填参数补全（确定性）：规划时 LLM 填不齐 ref 参数（如 routingCode，
                # 需先查询工艺路线才知道），从已查的 context records 里按 conceptPropertyRef / 字段名补全，
                # 避免「必填参数未提供」导致写操作失败。
                if _action_name:
                    try:
                        from app.services.action_executor import action_executor as _ae_fill
                        _sig_fill = _ae_fill._sigs.get(_action_name, {}) or {}
                        _missing = [p for p in (_sig_fill.get("params") or [])
                                    if p.get("required") and not _params.get(p.get("name"))]
                        if _missing:
                            _fill = _extract_params_from_context(context, steps_taken)
                            _alias = {"routingCode": ["routingCode", "code", "routeCode"],
                                      "materialCode": ["materialCode", "code"]}
                            for _mp in _missing:
                                _mn = _mp.get("name")
                                _ref = _mp.get("conceptPropertyRef") or ""
                                _rc = _rp = ""
                                if "." in _ref:
                                    _rc, _rp = _ref.split(".", 1)
                                # 1) 按 conceptPropertyRef 的目标概念 records 取 ref 字段值
                                if _rc:
                                    for _rec in (context.get(f"{_rc}_records") or []):
                                        _v = _rec.get(_rp) if isinstance(_rec, dict) else None
                                        if _v is not None and str(_v).strip() and str(_v).strip() != "-":
                                            _params[_mn] = _v
                                            break
                                # 2) 兜底：按字段别名在目标概念 records 里找
                                if not _params.get(_mn) and _rc:
                                    for _n in _alias.get(_mn, [_mn]):
                                        for _rec in (context.get(f"{_rc}_records") or []):
                                            _v = _rec.get(_n) if isinstance(_rec, dict) else None
                                            if _v is not None and str(_v).strip() and str(_v).strip() != "-":
                                                _params[_mn] = _v
                                                break
                                        if _params.get(_mn):
                                            break
                                # 3) 兜底：_extract_params_from_context 的字段名/中文标签
                                if not _params.get(_mn):
                                    _v = _fill.get(_mn) or _fill.get(_mp.get("label"))
                                    if _v:
                                        _params[_mn] = _v
                                if _params.get(_mn):
                                    logger.info(f"[DynamicPlanner] 写操作 {_action_name} 补全必填参数 {_mn}={_params[_mn]}")
                    except Exception as _fe:
                        logger.warning(f"[DynamicPlanner] 写操作参数补全失败: {_fe}")
                if not _action_name:
                    yield ('step', json.dumps({
                        "step": step_num, "action": "action_done",
                        "concept": "", "description": "写操作步骤缺少动作名", "ok": False,
                    }, ensure_ascii=False))
                    continue
                yield ('step', json.dumps({
                    "step": step_num, "action": "action_start",
                    "concept": _action_name,
                    "description": f"执行 {_action_name}: {_reason}",
                    "model": _get_configured_model("decision_model"),
                }, ensure_ascii=False))
                try:
                    from app.services.action_executor import action_executor as _ae2
                    _res = await _ae2.execute_structured_async(_action_name, _params, user_id="")
                    _res_text = _res.get("result", "") if isinstance(_res, dict) else str(_res)
                    context[f"action_{_action_name}_result"] = _res_text
                    steps_taken.append({
                        "step": step_num, "concept": _action_name,
                        "label": f"写操作 {_action_name}", "result": _res_text[:500],
                    })
                    yield ('step', json.dumps({
                        "step": step_num, "action": "action_done",
                        "concept": _action_name,
                        "description": f"执行 {_action_name}: {_reason}",
                        "ok": True,
                        "output_preview": _res_text[:2000],
                    }, ensure_ascii=False))
                except Exception as _ae:
                    logger.error(f"[DynamicPlanner] 写操作失败 {_action_name}: {_ae}")
                    _err = f"[写操作失败: {_ae}]"
                    context[f"action_{_action_name}_result"] = _err
                    steps_taken.append({
                        "step": step_num, "concept": _action_name,
                        "label": f"写操作 {_action_name}", "result": _err,
                    })
                    yield ('step', json.dumps({
                        "step": step_num, "action": "action_done",
                        "concept": _action_name,
                        "description": f"执行 {_action_name}: {_reason}",
                        "ok": False,
                        "output_preview": str(_ae)[:500],
                    }, ensure_ascii=False))

            elif decision["action"] == "find_similar":
                concept = self._resolve_concept(decision.get("concept", ""))
                # 用户说"找相似BOM" → 实际搜索WorkOrder（工单粒度），再在汇总中对比BOM
                if concept == "WorkOrderBOM":
                    concept = "WorkOrder"
                target_key = decision.get("targetKey", decision.get("target", ""))
                reason = decision.get("reason", "")
                skill = self._concept_skill_map.get(concept)
                label = skill.concept_label if skill else concept

                yield ('step', json.dumps({
                    "step": step_num, "action": "action_start",
                    "concept": concept or "WorkOrderBOM",
                    "description": f"匹配相似{label}: {reason}",
                    "model": _get_configured_model("decision_model"),
                }, ensure_ascii=False))

                try:
                    from app.services.vector_search_engine import vector_search_engine as _vse
                    sim_result, _ = await _vse.find_similar(
                        concept or "WorkOrderBOM", target_key,
                        arguments={"message": message},
                    )
                    context[f"{concept}_similar_result"] = sim_result
                    steps_taken.append({
                        "step": step_num, "concept": concept or "WorkOrderBOM",
                        "label": f"相似{label}匹配", "result": sim_result[:500],
                    })
                except Exception as e:
                    logger.error(f"[DynamicPlanner] 相似匹配失败 {concept}: {e}")
                    context[f"{concept}_similar_result"] = f"[相似匹配失败: {e}]"
                    steps_taken.append({
                        "step": step_num, "concept": concept or "WorkOrderBOM",
                        "label": f"相似{label}匹配", "result": f"[错误: {e}]",
                    })

                yield ('step', json.dumps({
                    "step": step_num, "action": "action_done",
                    "concept": concept or "WorkOrderBOM",
                    "description": f"匹配相似{label}: {reason}",
                    "output_preview": str(context.get(f"{concept}_similar_result", ""))[:2000],
                }, ensure_ascii=False))

            elif decision["action"] == "query":
                concept = decision.get("concept", "")
                reason = decision.get("reason", "")
                skill = self._concept_skill_map.get(concept)

                # 循环检测：同一概念已查询 ≥2 次 → 跳过重复查询（防空结果反复重查失控）。
                # 递归展开步（带 _depth 标记）不受概念计数限制——多层 BOM 同一概念（如物料）
                # 会作为不同子层查多次，其失控由 _expanded 的 (概念,target,join值) 去重与 maxDepth 保证
                _qcount = self._queried.get(concept, 0)
                self._queried[concept] = _qcount + 1
                if _qcount >= 2 and step.get("_depth") is None:
                    logger.warning(f"[DynamicPlanner] 循环检测：{concept} 已查询 {_qcount + 1} 次，跳过重复查询")
                    yield ('step', json.dumps({
                        "step": step_num, "action": "query_done",
                        "concept": concept,
                        "description": f"{getattr(skill, 'display_name', '') or concept}: {reason}（循环检测跳过重复查询）",
                        "ok": True,
                        "output_preview": str(context.get(f"{concept}_result", ""))[:2000] or "(已查过，跳过重复查询)",
                    }, ensure_ascii=False))
                    continue

                if concept in self._mcp_tools:
                    # P3：MCP 工具经 action_executor 统一执行（含 P0 写操作治理）
                    _mcp_sig = self._mcp_tools[concept]
                    _mcp_desc = (_mcp_sig.get("description", "") or "")[:80] or concept
                    yield ('step', json.dumps({
                        "step": step_num, "action": "query_start",
                        "concept": concept,
                        "description": f"MCP 工具: {_mcp_desc}",
                        "model": _get_configured_model("decision_model"),
                    }, ensure_ascii=False))
                    _mr = await action_executor.execute_structured_async(
                        concept, {"_message": message}, user_id="",
                    )
                    _mcp_result = str(_mr.get("result", ""))
                    context[f"{concept}_result"] = _mcp_result
                    context[f"{concept}_records"] = []
                    yield ('step', json.dumps({
                        "step": step_num, "action": "query_done",
                        "concept": concept,
                        "description": f"MCP 工具: {_mcp_desc}",
                        "ok": not _mr.get("needs_approval"),
                        "output_preview": _mcp_result[:2000],
                    }, ensure_ascii=False))
                    steps_taken.append({
                        "step": step_num, "concept": concept,
                        "label": concept, "result": _mcp_result[:500],
                    })
                    continue

                if not skill:
                    logger.warning(f"[DynamicPlanner] 未知概念: {concept}")
                    yield ('step', json.dumps({
                        "step": step_num, "action": "error",
                        "concept": concept,
                        "error": f'概念[{concept}]未配置查询工具',
                    }, ensure_ascii=False))
                    steps_taken.append({
                        "step": step_num, "concept": concept,
                        "label": concept, "result": "[未配置查询工具]",
                    })
                    continue

                yield ('step', json.dumps({
                    "step": step_num, "action": "query_start",
                    "concept": concept,
                    "description": f"{skill.display_name}: {reason}",
                    "model": _get_configured_model("decision_model"),
                }, ensure_ascii=False))

                # 统一走 action executor，无 sig 时构造最小 sig
                tool_name = f"{concept}_query"
                sig = action_executor._sigs.get(tool_name)
                if not sig:
                    sig = {"conceptName": concept, "functionName": tool_name}
                from app.services.data_backend import data_backend as _db

                # 简单查询：确定性执行，空/失败直接接受，不每步 LLM 反思（反思聚焦汇总前整体评估）
                _was_retry = False  # 全表重查标志：重查结果是兜底数据，不应触发递归展开
                try:
                    params = await self._extract_params(message, concept, context, steps_taken)
                    result, row_count, _, raw_records = await action_executor._query_via_backend(
                        concept, sig, params, _db,
                    )
                    result = self._strip_internal_ids(result, concept)
                    # 空结果区分：
                    # ① 参数是用户明确指定的编号（消息里字面出现）且查 0 条 → 对象不存在，诚实报"查无此单"
                    # ② 参数是幻觉/误填（plantCode 等）→ 去参数重查一次（抓参数误填）
                    # ③ 无参数查全表 0 条 → 提示数据源
                    if row_count == 0:
                        if params:
                            codes = re.findall(r'([A-Z]{2,8}(?:\d{2,8}(?:[-_][A-Za-z0-9]+)*|(?:[-_][A-Za-z0-9]+)+))', message)
                            explicit = [c for c in codes if any(str(c) == str(v) for v in params.values())]
                            if explicit:
                                hint = f"⚠️ 未找到编号 {'、'.join(explicit)} 对应的记录（该对象不存在或数据未同步）。"
                                context[f"{concept}_result"] = hint
                                context[f"{concept}_records"] = []
                            elif step.get("_depth") is not None:
                                # 递归展开步查询 0 条 = 该子层无数据（如子件物料无 BOM），
                                # 是递归自然终止信号——去参数全表重查会把无关记录引入注入链污染下游
                                logger.info(f"[DynamicPlanner] 递归展开步 {concept} 查询 0 条，子层终止（不重查）")
                                context[f"{concept}_result"] = result
                                context[f"{concept}_records"] = []
                            else:
                                logger.warning(f"[DynamicPlanner] {concept} 带参数查询 0 条 ({list(params.keys())})，去参数重查")
                                retry_result, retry_count, _, retry_raw = await action_executor._query_via_backend(
                                    concept, sig, {}, _db,
                                )
                                if retry_count > 0:
                                    result = self._strip_internal_ids(retry_result, concept)
                                    raw_records = retry_raw
                                    _was_retry = True  # 全表兜底数据，标记不触发递归展开
                                    hint = (f"⚠️ 原查询条件（{', '.join(str(k) for k in params.keys())}）未匹配到数据，"
                                            f"已去除条件重查，找到 {retry_count} 条。原条件可能不正确，请核对查询条件。")
                                else:
                                    hint = "⚠️ 全量重查仍无数据，数据源可能未同步此概念或 namespace 不匹配。"
                                context[f"{concept}_result"] = result + "\n\n" + hint
                        else:
                            hint = "⚠️ 全表查询无数据，数据源可能未同步此概念或 namespace 不匹配。"
                            context[f"{concept}_result"] = result + "\n\n" + hint
                    else:
                        # 同一概念多次查询（成品物料 + 子件物料），result 用递增后缀分开存，
                        # 避免第二次（子件）覆盖第一次（成品）导致汇总时成品/子件混淆；
                        # records 保持最后一次覆盖，供注入使用（后查的子件值才是下游要用的）
                        _res_key = f"{concept}_result"
                        if context.get(_res_key):
                            _i = 2
                            while context.get(f"{_res_key}_{_i}"):
                                _i += 1
                            _res_key = f"{_res_key}_{_i}"
                        context[_res_key] = result
                    context[f"{concept}_records"] = raw_records
                    _qok = True
                except Exception as e:
                    logger.error(f"[DynamicPlanner] 查询失败 {concept}: {e}")
                    context[f"{concept}_result"] = f"[查询失败: {e}]"
                    context[f"{concept}_records"] = []
                    _qok = False

                yield ('step', json.dumps({
                    "step": step_num, "action": "query_done",
                    "concept": concept,
                    "description": f"{skill.display_name}: {reason}",
                    "ok": _qok,  # 执行成功即完成（含空结果）；数据是否充分由汇总前整体反思评估
                    "output_preview": str(context.get(f"{concept}_result", ""))[:2000],
                    "content": str(context.get(f"{concept}_result", ""))[:20000],
                }, ensure_ascii=False))

                steps_taken.append({
                    "step": step_num, "concept": concept,
                    "label": skill.concept_label,
                    "result": str(context.get(f"{concept}_result", ""))[:500],
                })

                # ── 递归展开（本体 traversal=recursive 声明驱动，多层动态下钻）──
                # 查完本概念后，沿其 recursive 出边检查结果里的 join 值是否有未展开子层：
                # 如 BOM分录查到子件 MAT-9002，子件又是半成品（有 BOM-902）→ 动态再插
                # BOM头/BOM分录/物料步骤，直到叶子或关系声明的 maxDepth。
                # 全表重查（_was_retry）是兜底数据，触发展开会拿无关记录的 join 值错配。
                if raw_records and not _was_retry:
                    _pend = self._recursive_pending(concept, raw_records, int(step.get("_depth") or 0))
                    if _pend:
                        # 递归展开步骤已覆盖原计划中的同概念待执行步骤 → 移除防重复执行
                        _pend_concepts = {p["concept"] for p in _pend}
                        _tail = [s2 for s2 in _steps_live[_exec_idx:]
                                 if not (s2.get("concept") in _pend_concepts and s2.get("_depth") is None)]
                        _steps_live[_exec_idx:] = _tail
                        _steps_live[_exec_idx:_exec_idx] = _pend
                        self._rec_added += len(_pend)
                        logger.info(f"[DynamicPlanner] 递归展开 L{int(step.get('_depth') or 0) + 1} 插入 {len(_pend)} 步: {[p['concept'] for p in _pend]}")

        # 汇总前整体反思：评估数据能否支撑回答；缺且可补查则补查，否则产出回答边界结论
        if steps_taken:
            _gr = await self._reflect_global(message, context, steps_taken)
            _boundary = (_gr.get("boundary") or "").strip()
            if _gr.get("need_more"):
                _extra = (_gr.get("concepts") or [None])[0]
                # 预算 + 循环检测：预算超限或补查概念已查 ≥2 次 → 不补查，走回答边界
                _extra_repeated = bool(_extra) and self._queried.get(_extra, 0) >= 2
                _budget_ok, _ = self._budget_exhausted(len(steps_taken))
                if _extra and not _extra_repeated and not _budget_ok and (_extra in self._concept_skill_map or _extra in self._mcp_tools):
                    _ex_skill = self._concept_skill_map.get(_extra)
                    _ex_desc = getattr(_ex_skill, "display_name", "") or _extra
                    yield ('think', json.dumps({
                        "step": len(steps) + 1, "concept": "", "concept_label": "整体评估",
                        "content": f"整体反思：结果缺关键数据，补查{_ex_desc}",
                    }, ensure_ascii=False))
                    yield ('step', json.dumps({
                        "step": len(steps) + 1, "action": "query_start",
                        "concept": _extra,
                        "description": f"补查{_ex_desc}: {(_boundary or '整体评估发现缺关键数据')[:80]}",
                        "model": _get_configured_model("decision_model"),
                    }, ensure_ascii=False))
                    _eok = False
                    try:
                        from app.services.data_backend import data_backend as _edb
                        _ex_sig = action_executor._sigs.get(f"{_extra}_query") or {"conceptName": _extra, "functionName": f"{_extra}_query"}
                        self._queried[_extra] = self._queried.get(_extra, 0) + 1  # 循环检测计数
                        _ep = await self._extract_params(message, _extra, context, steps_taken)
                        _er, _ec, _, _eraw = await action_executor._query_via_backend(_extra, _ex_sig, _ep, _edb)
                        _er = self._strip_internal_ids(_er, _extra)
                        context[f"{_extra}_result"] = _er
                        context[f"{_extra}_records"] = _eraw
                        _eok = True
                    except Exception as _ex:
                        logger.error(f"[DynamicPlanner] 补查失败 {_extra}: {_ex}")
                        context[f"{_extra}_result"] = f"[补查失败: {_ex}]"
                        context[f"{_extra}_records"] = []
                    yield ('step', json.dumps({
                        "step": len(steps) + 1, "action": "query_done",
                        "concept": _extra,
                        "description": f"补查{_ex_desc}",
                        "ok": _eok,
                        "output_preview": str(context.get(f"{_extra}_result", ""))[:2000],
                    }, ensure_ascii=False))
                    steps_taken.append({
                        "step": len(steps) + 1, "concept": _extra,
                        "label": getattr(_ex_skill, "concept_label", "") or _extra,
                        "result": str(context.get(f"{_extra}_result", ""))[:500],
                    })
                else:
                    # 补查概念无效：产出回答边界，交给汇总明确结论
                    if not _boundary:
                        _boundary = "结果缺关键数据且无可补查概念，以下结论仅基于现有数据，缺失项见数据缺失说明"
                    context["_answer_boundary"] = _boundary
                    yield ('think', json.dumps({
                        "step": len(steps) + 1, "concept": "", "concept_label": "整体评估",
                        "content": f"整体反思：{_boundary}",
                    }, ensure_ascii=False))
            else:
                # 不需补查：boundary 明确回答边界（空=数据充分），传给汇总
                if _boundary:
                    context["_answer_boundary"] = _boundary
                    yield ('think', json.dumps({
                        "step": len(steps) + 1, "concept": "", "concept_label": "整体评估",
                        "content": f"整体反思：{_boundary}",
                    }, ensure_ascii=False))
                else:
                    yield ('think', json.dumps({
                        "step": len(steps) + 1, "concept": "", "concept_label": "整体评估",
                        "content": "整体反思：现有数据已能支撑回答，直接汇总",
                    }, ensure_ascii=False))


        # ── 影响判定（确定性计算专用化率 + 日期归因，注入汇总）──
        # 预算限制分支可能已提前执行过，避免重复计算
        if not (context or {}).get("_impact_judgement"):
            try:
                from app.services.impact_judger import judge_impact
                _judgement = await judge_impact(context, steps_taken, message)
                if _judgement:
                    steps_taken.append({
                        "step": len(steps_taken) + 1,
                        "concept": "_impact_judgement",
                        "label": "影响判定",
                        "result": _judgement,
                    })
                    context["_impact_judgement"] = _judgement
                    yield ('think', json.dumps({
                        "step": len(steps_taken), "concept": "", "concept_label": "影响判定",
                        "content": f"影响判定（确定性）：\n{_judgement}",
                    }, ensure_ascii=False))
                    logger.info(f"[DynamicPlanner] 影响判定完成: {_judgement[:120]}")
            except Exception as e:
                logger.warning(f"[DynamicPlanner] 影响判定失败: {e}")


        if not summary_produced and steps_taken:
            # 最后一步强制汇总
            yield ('step', json.dumps({
                "step": self.MAX_STEPS + 1, "action": "summary",
                "description": "综合汇总",
                "model": model_name or _get_configured_model("summary_model"),
            }, ensure_ascii=False))
            yield ('content', "\n\n---\n### 综合汇总\n\n")
            async for chunk_type, chunk_content in self._llm_summarize(
                self._build_decision_prompt(
                    self.build_planner_prompt(), message, steps_taken, context, self.MAX_STEPS, history_messages,
                ), context, model_name, enable_thinking, session_id, steps_taken,
            ):
                yield (chunk_type, chunk_content)
            # 影响判定结论固定输出（确定性，不依赖 LLM 引用）
            _ij_fixed2 = (context or {}).get("_impact_judgement", "")
            if _ij_fixed2:
                yield ('content', f"\n\n---\n### 影响判定结论\n\n{_ij_fixed2}\n")

        yield ('done', json.dumps({
            "steps_taken": len(steps_taken),
            "max_steps": self.MAX_STEPS,
        }, ensure_ascii=False))

    # ── 可靠性：预算硬上限 ──

    def _budget_exhausted(self, steps_taken: int = 0) -> tuple:
        """预算硬上限检查：返回 (是否超限, 原因)。

        三项确定性规则（计数 + 阈值，不依赖 LLM），保证单次分析总会出结果、不会无限跑：
        - 最大步骤数：已执行计划步数达上限即停（递归展开步不占此额，另有单独限额）
        - 递归展开步上限：traversal=recursive 动态下钻步数（防多层展开失控）
        - 总执行时间预算：超限即停（防慢链路拖死会话）
        - LLM 调用上限：计划/评审/填槽/反思/汇总合计（防 token 失控）
        """
        if steps_taken >= self.MAX_STEPS:
            return True, f"达到最大步骤数 {self.MAX_STEPS} 上限"
        if self._rec_added >= 6:
            return True, "递归展开步数达到 6 步上限"
        if time.time() - self._t0 > self._time_budget_s:
            return True, f"执行时间超过 {int(self._time_budget_s)}s 预算"
        if self._llm_calls >= self._max_llm_calls:
            return True, f"LLM 调用达到 {self._max_llm_calls} 次上限"
        return False, ""

    # ── P2 反思循环 ──

    @staticmethod
    def _extract_json(raw):
        """稳健解析 LLM 返回的 JSON：剥离围栏/BOM/多余文本，提取首尾花括号。

        thinking 模型的输出格式不稳定（可能带 ```json 围栏、BOM、前后说明文字），
        直接 json.loads 会失败导致反思功能降级为 NEXT。
        """
        if not raw:
            raise ValueError("空响应")
        text = str(raw).strip().lstrip("﻿")
        if text.startswith("```"):
            text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
            text = re.sub(r"\s*```$", "", text).strip()
        s, e = text.find("{"), text.rfind("}")
        if s >= 0 and e > s:
            text = text[s:e + 1]
        return json.loads(text)

    async def _reflect_global(self, message: str, context: dict, steps_taken: list) -> dict:
        """汇总前整体反思：评估现有数据能否支撑回答用户需求。

        返回 {"need_more": bool, "concepts": [补查概念], "boundary": 回答边界结论}
        - need_more=true + concepts：缺关键数据且可补查（补查最多 1 个概念）
        - need_more=false + boundary：缺数据但不可补查时，产出明确的回答边界结论
        - need_more=false + boundary=""：现有数据已充分
        """
        # 确定性充分性判断（不依赖 LLM，避免反思误判）：所有查询步骤都有数据（找到 N 条且 N>0）
        # → 数据充分，直接跳过 LLM 反思。只有存在 0 条/查询失败时才交给 LLM 评估是否补查。
        try:
            _results = [str(v) for k, v in (context or {}).items() if k.endswith("_result") and str(v).strip()]
            if _results and all(re.search(r'找到\s*(\d+)\s*条记录', r) and int(re.search(r'找到\s*(\d+)\s*条记录', r).group(1)) > 0 for r in _results):
                return {"need_more": False, "concepts": [], "boundary": ""}
        except Exception:
            pass
        try:
            from app.services.llm_service import llm_service
            # 概念名 → 中文 label（反思内容避免英文概念，LLM 会引用所见名称）
            _label_map = {
                c: getattr(s, "concept_label", "") or c
                for c, s in self._concept_skill_map.items()
            }
            results_summary = "\n".join(
                f"- {_label_map.get(k.replace('_result', ''), k.replace('_result', ''))}: {str(v)[:120]}"
                for k, v in context.items()
                if k.endswith("_result") and str(v).strip()
            )[:1200]
            done_concepts = [_label_map.get(s.get("concept"), s.get("concept")) for s in (steps_taken or [])]
            prompt = (
                f"当前日期: {datetime.now().strftime('%Y-%m-%d')}（用户说'本月'指当前自然月）。\n"
                f"多跳分析已执行的步骤（概念）: {done_concepts or '(无)'}\n"
                f"已收集结果:\n{results_summary or '(空)'}\n"
                f"用户需求: {message[:200]}\n"
                f"评估：现有数据能否支撑回答用户需求？\n"
                f"- 能支撑 → {{'need_more': false, 'boundary': ''}}\n"
                f"- 缺关键数据且可补查（存在明确可查询的概念能补上缺口）→ {{'need_more': true, 'concepts': ['概念名'], 'boundary': ''}}\n"
                f"- 缺数据且不可补查 → {{'need_more': false, 'boundary': '明确的回答边界结论：哪些能答、哪些不能答、根因、建议'}}\n"
                f"只输出 JSON"
            )
            model = _get_configured_model("decision_model")
            self._llm_calls += 1  # 预算计数：整体反思
            async with span("dynamic_reflect", "generic"):
                raw = await asyncio.wait_for(
                    llm_service.chat_sync(
                        message=prompt,
                        system_prompt="你是多跳分析整体评估器，只输出 JSON。",
                        model_name=model,
                    ),
                    timeout=30.0,  # 整体反思为低频关键决策，宽松超时
                )
            parsed = self._extract_json(raw)
            return {
                "need_more": bool(parsed.get("need_more")),
                "concepts": [str(c).strip() for c in (parsed.get("concepts") or [])][:1],
                "boundary": str(parsed.get("boundary", "")).strip(),
            }
        except Exception as e:
            logger.warning(f"[DynamicPlanner] 整体反思失败: {e}")
            return {"need_more": False, "concepts": [], "boundary": ""}

    @staticmethod
    def _strip_internal_ids(result: str, concept: str) -> str:
        """替换 Display 值 + 删除内部 ID 列。"""
        _drop_headers = {'id', 'name', '_namespace', 'namespace', 'embedding'}
        lines = result.split('\n')
        header_cells = None
        display_map = {}   # {prop}_idx → {prop}Display_idx
        drop_cols = set()
        out = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith('|') and not stripped.startswith('|---') and not stripped.startswith('|--'):
                cells = [c.strip() for c in stripped.split('|')[1:-1]]
                if header_cells is None:
                    header_cells = cells
                    # 标记内部ID列
                    for i, h in enumerate(cells):
                        if h.lower() in _drop_headers:
                            drop_cols.add(i)
                    # 找 {prop}Display → {prop} 映射
                    for di, dh in enumerate(cells):
                        if dh.lower().endswith('display'):
                            prop = dh[:-7]
                            for pi, ph in enumerate(cells):
                                if ph.lower() == prop.lower():
                                    display_map[pi] = di
                                    drop_cols.add(di)  # 用完后删掉Display列
                                    break
                    new_cells = [c for i, c in enumerate(cells) if i not in drop_cols]
                    out.append('| ' + ' | '.join(new_cells) + ' |')
                else:
                    # 数据行：Display替换 + 删除标记列
                    vals = list(cells)
                    for pi, di in display_map.items():
                        if di < len(vals) and vals[di] and vals[di] != '-':
                            vals[pi] = vals[di]
                    vals = [c for i, c in enumerate(vals) if i not in drop_cols]
                    out.append('| ' + ' | '.join(vals) + ' |')
            elif stripped.startswith('|---') or stripped.startswith('|--'):
                n = len([c.strip() for c in out[-1].split('|')[1:-1]]) if out else 0
                if n:
                    out.append('|' + '|'.join(['------'] * n) + '|')
            else:
                out.append(line)
        return '\n'.join(out)

    def _resolve_concept(self, name: str) -> str:
        """中文概念名→英文名映射。LLM 可能输出'工单'而非'WorkOrder'。"""
        if name in self._concept_skill_map:
            return name
        # 清洗混合名称：提取纯英文或纯中文部分分别匹配
        import re as _re2
        _clean = _re2.sub(r'[一-鿿]+', '', name).strip()  # 去中文留英文
        _clean_cn = _re2.sub(r'[a-zA-Z]+', '', name).strip()      # 去英文留中文
        for skill in self.runtime.skills:
            if skill.concept in (name, _clean) or skill.concept_label in (name, _clean_cn) or skill.display_name in (name, _clean_cn):
                return skill.concept
        from app.services.action_executor import action_executor
        action_executor._ensure_loaded()
        for sig_name in action_executor._sigs:
            sig = action_executor._sigs[sig_name]
            cn = sig.get('conceptName', '')
            cl = sig.get('conceptLabel', '')
            if cn in (name, _clean) or cl in (name, _clean_cn) or f'{cn}查询' == name or f'{cl}查询' == name:
                return cn
        return _clean or name

    def _build_decision_prompt(
        self, planner: str, message: str,
        steps: list[dict], context: dict, step_num: int,
        history_messages: list = None,
    ) -> str:
        """构建 LLM 决策提示词。"""
        parts = [planner, ""]

        if history_messages:
            parts.append("## 对话历史")
            parts.append("当前消息是对上述对话的延续，请结合上下文理解用户完整意图，直接执行，不要再次反问。")
            for hm in history_messages[-6:]:
                role = getattr(hm, 'type', '') or getattr(hm, 'role', 'user')
                content = getattr(hm, 'content', '')
                if content:
                    parts.append(f"- {role}: {str(content)[:300]}")
            parts.append("")

        parts.append(f"## 当前用户输入\n{message}")
        parts.append("")

        # 回答边界（整体反思产出）：数据不足时报告须明确哪些能答/不能答及根因
        _ab = (context or {}).get("_answer_boundary", "")
        if _ab:
            parts.append(f"## 回答边界（必须遵守）\n{_ab}\n报告必须在开头明确此边界：能回答什么、不能回答什么及根因，不要含糊带过。")
            parts.append("")

        # 影响判定结论（确定性计算，必须原样呈现在报告中，不可省略或改写）
        _ij = (context or {}).get("_impact_judgement", "")
        if _ij:
            parts.append("## 影响判定结论（必须原样呈现在报告中，不可省略）")
            parts.append(_ij)
            parts.append("")

        if steps:
            parts.append("## 已完成的查询")
            for s in steps:
                if s.get("concept") == "_impact_judgement":
                    continue  # 影响判定已在上方独立段落呈现，避免重复
                parts.append(
                    f"步骤{s['step']}: 查询{s['label']}({s['concept']})\n"
                    f"结果: {s['result'][:300]}"
                )
            parts.append("")

        parts.append(f"## 当前是第 {step_num}/{self.MAX_STEPS} 步")
        if step_num >= self.MAX_STEPS:
            parts.append("已是最后一步，必须用 SUMMARY: 输出最终分析结论。")
        else:
            parts.append("请决定: 查询下一个概念 (QUERY: 概念名) 或 汇总输出 (SUMMARY:)")

        return "\n".join(parts)

    def _retrieve_knowledge_text(self, message: str) -> str:
        """按用户问题向量检索领域知识，拼接为注入文本（无则空）。"""
        try:
            from app.services.ontology_service import ontology_service
            knowledge = ontology_service.retrieve_domain_knowledge(message)
            return "\n".join(knowledge)
        except Exception:
            return ""

    async def _llm_summarize(
        self, decision_prompt: str, context: dict,
        model_name: Optional[str], enable_thinking: Optional[bool],
        session_id: str, steps_taken: list = None,
    ) -> AsyncGenerator[tuple, None]:
        """流式输出 LLM 汇总总结。"""
        from app.services.llm_service import llm_service

        self._llm_calls += 1  # 预算计数：汇总

        data_text_parts = []
        for k, v in context.items():
            # 跳过 *_records（原始 dict 列表）：与 *_result 表格是同一份数据，
            # 且 Python repr（单引号、英文 key）对 LLM 可读性差，重复塞入徒增 token
            if k != "message" and v and not k.endswith("_records"):
                data_text_parts.append(f"### {k}\n{v}")
        data_text = "\n\n".join(data_text_parts)

        msg = context.get('message', '')
        is_anomaly = any(w in msg for w in ('为什么', '延期', '异常', '故障', '挂起', '根因'))
        anomaly_requirement = ""
        if is_anomaly:
            anomaly_requirement = (
                "\n## 根因追溯"
                "\n复制此格式画因果链，每行用引号包裹节点文字："
                "\n```mermaid"
                "\nflowchart TD"
                '\n  A["异常现象"] --> B["直接原因"]'
                '\n  B --> C["根本原因"]'
                "\n```"
            )
        skill_catalog = self.runtime.skill_catalog_text
        summary_prompt = (
            f"## 本体关系说明\n{skill_catalog}\n\n"
            f"## 用户问题\n{msg}\n\n"
            f"## 当前日期\n{datetime.now().strftime('%Y-%m-%d %H:%M')}（分析时请以此为准判断时间先后）\n\n"
            f"## 查询数据\n{data_text}\n\n"
            f"请根据以上数据及本体关系输出分析结论。"
            f"{self._retrieve_knowledge_text(msg)}"
            f"数据充分时分层报告（概览→发现→行动）；"
            f"数据不足时简洁总结 + P0/P1/P2 行动项，无数据直接告知。"
            f"**全文控制在 {self._summary_max_chars} 字以内**：只保留关键结论、关键数值和行动项，删除过程性铺陈与冗余展开。"
        )
        ops_list = "\n".join(
            f"- {s.display_name or s.concept_label}{_action_label(a)}（{s.concept}_{a}）"
            for s in self.runtime.skills
            for a in (s.actions if hasattr(s, 'actions') and s.actions else ['query'])
        )
        _ops_section = f"\n## 可用操作（优先选用，若需其他操作也可提出）\n{ops_list}\n"
        # 意图分类：规则兜底 → 独立 LLM 分类（规则未命中时）
        _intent = _classify_change_intent(msg)
        if _intent is None:
            _intent = await self._llm_classify_intent(msg, session_id, model_name)
        _emit_plan = _intent in ('plan', 'execute')
        logger.info(f"[DynamicPlanner] 意图分类: {_intent} (emit_plan={_emit_plan})")

        if _emit_plan:
            _intent_line = (
                "\n## 变更方案输出要求"
                "\n**意图判定（系统已判定）：变更/方案意图**——用户要求变更方案或执行改动，"
                "\n必须在报告末尾用 ```json 代码块输出变更方案数组（数据不足的项列「数据缺失」，不硬编）。"
            )
        else:
            _intent_line = (
                "\n## 变更方案输出要求"
                "\n**意图判定（系统已判定）：纯分析意图**——用户仅要求分析/查询/报告，不要求方案或改动，"
                "\n绝对禁止输出变更方案 JSON，即使发现数据问题也只给文字建议。"
            )
        _change_section = _intent_line + (
            "\n变更方案数组格式："
            "\n[{\"id\":\"plan_1\",\"label\":\"方案标题\",\"recommended\":true,\"risk\":\"low|medium|high\","
            "\n  \"precondition\":\"前提条件\",\"impact\":\"影响说明\","
            "\n  \"steps_preview\":[\"步骤1\",\"步骤2\"],"
            "\n  \"actions\":[\"ConceptName_actionName\"],"
            "\n  \"action_labels\":[\"操作中文名\"],"
            "\n  \"params_suggestion\":{\"工单号\":\"MO001\",\"物料编码\":\"380000\"},"
            "\n  \"verify_target\":{\"concept\":\"WorkOrderBOMItem\",\"property\":\"quantity\",\"expected\":\"8\",\"label\":\"BOM需求数量已调整\",\"filters\":{\"workOrderCode\":\"MO001\"}}}]"
            "\n其中："
            "\n- **steps_preview 必须是可直接执行的变更动作（增/删/改/替换/调整/复制/初始化/回退/冲销等）。**"
            "\n  **禁止**查询、核实、确认、检查、查看、评估、对比、分析、判断、验证类动词——这些是分析阶段的事，报告正文已包含，绝不能放进方案步骤。"
            "\n  反例: [\"查询BOM\",\"核实领料状态\",\"确认是否存在\"] — 查询/核实不是变更动作，禁止。"
            "\n  正例: [\"新增缺失物料E34-053\",\"将护套用量调整为8\"] — 直接给出变更动作与目标值。"
            "\n- **数据缺失时禁止硬编方案**：若无法从查询数据确定变更对象/目标值（如查不到BOM明细、物料编码缺失、用量未知），"
            "\n  则**不要输出该变更方案 JSON 块**，改在报告末尾用「🔍 数据缺失」小节明确列出缺少哪些数据、需要补查什么。"
            "\n  宁缺毋滥——查询/核实类内容已在报告正文表达，不构成变更方案。"
            "\n- **禁止推测补全**：查询不到的数据（如工单BOM明细、物料编码、用量）**严禁用通用知识/行业常识/推断/猜测补全**，"
            "\n  只能报告查询实际返回的事实；缺失项在报告末尾「🔍 数据缺失」小节如实列出。"
            "\n- actions 必须从上方「可用操作」列表中选择，用于后续关联执行链。"
            "\n- **action_labels 和 actions 一一对应**，提供每个操作的中文显示名。"
            "\n  如 actions:[\"WorkOrderBOM_addBomMaterial\"] → action_labels:[\"新增BOM物料\"]。"
            "\n- steps_preview 和 actions 必须一一对应，数量相等，每步对应一个 action。"
            "\n- params_suggestion 从查询数据中提取关键参数值（用中文键名），供执行时预填。只填查询数据中明确存在的值，不要猜测。"
            "\n- **每个变更方案都必须给出 verify_target**：声明执行后应复查的目标状态，"
            "\n  从该方案的变更动作推导（如\"新增物料\"→核实该物料项已生成；\"调整数量\"→核实数量已改为新值）。"
            "\n  label 必填且用中文可读描述（如\"核实工单BOM物料项名称已更新\"、\"拆分数已改为8\"），禁止用英文概念名/字段名当 label；"
            "\n  concept 用英文概念名（查询用）、property 用英文属性名、expected 填期望值、"
            "\n  filters 为定位记录的查询参数（键查询参数名、值具体编码）。"
            "\n  **字段真实性约束（防止幻觉）**：concept/property/filters 键必须是上方「可用操作」对应概念的真实属性名，"
            "\n  严禁编造不存在的概念或字段（如把工单号写成 workOrderCode 却配到 WorkOrder 上——WorkOrder 的工单号字段是 code）。"
            "\n  **枚举约束**：expected 若目标是状态/枚举类属性（如 status），必须填该属性的枚举显示值"
            "\n  （如「计划中/已排产/执行中/已完成/准备中」），严禁编造不存在的状态值（如「已关闭」「生产中」）。"
            "\n  系统执行完变更后会自动复查并判定目标是否达成。仅当方案确实无法确定可复查的具体字段时省略 verify_target，并在报告说明原因。"
            "\n如无变更需求则不输出此 JSON 块。"
            "\n**相似匹配规则**：若分析涉及推荐相似实例作为模板，必须输出变更方案，actions 从上方可用操作中选择对应的新增/复制操作。"
            "\n## 报告输出规范"
            "\n### 0. 禁止推测补全（最高优先级，约束整个报告正文）"
            "\n- 报告中**严禁用通用知识、行业常识、推断、猜测补全查询不到的数据**。"
            "\n- 例如：工单BOM明细未查到，就**不得**写\"潜在组件物料/可能涉及的材料（PVC、光纤、护套料等）\"——这些不是查询结果。"
            "\n- 只能报告查询实际返回的事实；查不到的项在「🔍 数据缺失」小节如实说明，宁缺毋滥，不做知识性补全。"
            "\n- **数值必须与查询结果一致**：报告引用的数量、日期、状态等数值必须与查询返回的数据**完全一致**，禁止改动、推断或凭空调整；查询返回什么值就报告什么值。"
            "\n### 1. 中文命名"
            "\n报告中**绝对禁止**出现英文概念名（如 WorkOrder、WorkOrderBOM）和英文属性名（如 materialCode、workOrderCode）。"
            "\n必须全部使用中文名称，例如：工单BOM、物料编码、工单号、计划数量、开工日期。"
            "\n### 2. 隐藏数据库ID"
            "\n- **禁止暴露数据库自增ID**（如 990、10079 等无业务含义的数字主键）。"
            "\n- 用业务编码替代：工单号（MO001）代替 id（990），物料编码（E34-053-0000-00）代替 name（10079）。"
            "\n- 表格列头只用中文标签，不用字段名。"
            "\n### 3. 排版格式"
            "\n- 关键数值用**粗体**突出"
            "\n- 状态用 ✅❌⚠️ 标记"
            "\n- 数据对比用 Markdown 表格"
            "\n- **查询无数据时不输出表头表格**：某概念查询 0 条结果时，报告中禁止列出该表头（表头 + 全空 `—` 行），直接用文字说明\"XX 无记录\"；表格只在有真实数据行时使用。"
            "\n- 发现和结论用 🔍📊⚠️ 等 emoji 分节"
            "\n- 行动建议用 P0🔴 / P1🟡 / P2🟢 标记优先级"
            "\n### 4. 禁用"
            "\n- 报告中绝对禁止出现英文操作名（如 WorkOrderBOM_findSimilar、adjustBomQty），"
            "\n  这些仅在 JSON 的 actions 字段中使用，正文里必须用中文（如\"匹配相似工单BOM\"\"调整BOM用量\"）"
            "\n- 正文中禁止出现英文概念名（WorkOrderBOM → 工单BOM）、英文属性名（materialCode → 物料编码）"
            "\n### 5. 数据缺失标注"
            "\n- 若关键概念查不到数据（如工单BOM明细为空、物料编码缺失、用量未知），"
            "\n- 报告数据缺失时必须区分「未查询」与「无数据」：本次分析链路根本没查该概念 → 写「本次未查询XX」；查询了但返回 0 条 → 才写「XX 无记录」。严禁把「未查询」误报成「无数据」——没查不等于没有。"
            "\n  在报告末尾用「🔍 数据缺失」小节列出缺哪些数据、影响哪些分析结论，"
            "\n  并明确\"因缺少 XX 数据，无法给出精确变更方案\"。禁止用查询/核实类内容冒充变更方案。"
        )
        summary_prompt = summary_prompt + _ops_section + _change_section + anomaly_requirement

        anomaly_sys = "根因分析必须用表格+flowchart图，节点用引号包裹。" if is_anomaly else ""
        # 动态读取项目领域描述，不写死"制造业"
        domain_desc = ""
        if self.runtime.agents:
            domain_desc = self.runtime.agents[0].project_description or ""
        if not domain_desc:
            try:
                from app.services.neo4j_service import neo4j_service
                records = await neo4j_service.execute_read(
                    "MATCH (p:Project) RETURN p.description AS desc LIMIT 1"
                )
                if records:
                    domain_desc = records[0].get("desc", "")
            except Exception:
                pass
        domain_hint = f"你专注于{domain_desc}领域。" if domain_desc else ""
        logger.info(f"[DynamicPlanner] _llm_summarize: model={model_name}, enable_thinking={enable_thinking}")
        full_response = ""
        async with span("dynamic_summarize", "generic"):
            async for chunk_type, chunk_content in llm_service.chat_stream(
                message=summary_prompt, session_id=session_id,
                model_name=model_name or _get_configured_model("summary_model"),
                enable_thinking=enable_thinking,
                system_prompt=(
                    f"你是制造业数据分析专家{('，'+domain_hint) if domain_hint else ''}。"
                    "根据数据量自适应：数据多→分层详报，数据少→简洁总结。不编造。"
                    "⚠️ 绝对禁止使用英文概念名和属性名——全部用中文！"
                    "用表格、emoji、粗体让报告清晰易读。"
                    "⚠️ 绝对禁止输出 LaTeX/数学公式（$...$、\\text{}、\\mathbf{}、\\frac 等），"
                    "所有计算过程用纯文本或表格表达（如：50台 × 6 pcs/台 = 300 pcs）。"
                    + anomaly_sys
                ),
                tools=None,
            ):
                if chunk_type == 'content':
                    # 硬过滤 LaTeX 残留（prompt 禁令是软约束，这里兜底去除 $ 与常见 LaTeX 命令）
                    _c = (str(chunk_content)
                          .replace('$', '')
                          .replace('\\text', '')
                          .replace('\\mathbf', '')
                          .replace('\\frac', '')
                          .replace('\\times', '×')
                          .replace('\\cdot', '·')
                          .replace('\\left', '')
                          .replace('\\right', ''))
                    full_response += _c
                    yield (chunk_type, _c)
                else:
                    if chunk_type == 'thinking':
                        logger.info(f"[DynamicPlanner] 收到 thinking chunk, len={len(str(chunk_content))}")
                    yield (chunk_type, chunk_content)

        # 解析 LLM 输出的 JSON（变更方案或行动项）
        import json as _json
        import re as _re
        _m = _re.search(r'```(?:json)?\s*\n(.*?)\n```', full_response, _re.DOTALL)
        if not _m:
            # LLM 可能没包代码块，直接找 JSON 数组
            _m = _re.search(r'(\[\s*\{.*?"label".*?\}\s*\])', full_response, _re.DOTALL)
        _llm_plans = []
        if _m:
            try:
                _parsed = _json.loads(_m.group(1))
                if isinstance(_parsed, list) and len(_parsed) > 0:
                    # 判断是变更方案（有 label/risk）还是行动项（有 action/params）
                    if "label" in _parsed[0] and "risk" in _parsed[0]:
                        _llm_plans = _parsed
            except Exception:
                pass

        # 后端把关：过滤"查询/核实类"退化方案（无实际变更动作，仅查询/核实/确认步骤）
        if _llm_plans:
            _before = len(_llm_plans)
            _llm_plans = [p for p in _llm_plans if not _is_degenerate_plan(p)]
            if len(_llm_plans) < _before:
                logger.info(f"[DynamicPlanner] 已过滤 {_before - len(_llm_plans)} 个查询/核实类退化方案，剩余 {len(_llm_plans)} 个变更方案")

        # 变更方案：LLM 推导 + 链自动匹配 + 参数提取
        if _llm_plans:
            _plans = await _match_chains_to_plans(_llm_plans)
            # 从查询数据中提取参数值补充到方案
            _records_keys = [k for k in context.keys() if k.endswith('_records')]
            logger.info(f"[DynamicPlanner] context._records keys: {_records_keys}, total context keys: {list(context.keys())[:10]}")
            _params_from_context = _extract_params_from_context(context, steps_taken or [])
            logger.info(f"[DynamicPlanner] _extract_params_from_context result: {_params_from_context}")
            for p in _plans:
                if not p.get("params_suggestion"):
                    p["params_suggestion"] = {}
                for k, v in _params_from_context.items():
                    if k not in p["params_suggestion"]:
                        p["params_suggestion"][k] = v
                # verify_target 生成后校验：纠正幻觉字段/枚举，不合法则丢弃
                _vt = p.get("verify_target")
                if _vt and isinstance(_vt, dict):
                    _vt = _sanitize_verify_target(_vt)
                    if _vt is None:
                        p.pop("verify_target", None)
                    else:
                        # LLM 缺中文 label 或输出纯英文字段路径（如 "WorkOrderBOMItem.name"）
                        # 时，重建为中文 label（概念中文名.属性中文名），避免前端显示原始字段名
                        _vt_label = _vt.get("label") or ""
                        _is_field_path = bool(re.match(
                            r'^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)+$', _vt_label,
                        ))
                        if not _vt_label or _is_field_path:
                            _vtc = str(_vt.get("concept", "") or "")
                            _vtsk = self._concept_skill_map.get(_vtc)
                            _cl = (_vtsk.concept_label if _vtsk else _vtc) or _vtc
                            _pl = str(_vt.get("property", "") or "")
                            try:
                                from app.services.ontology_service import ontology_service
                                _cdef = ontology_service.get_concept(_vtc)
                                if _cdef:
                                    for _pp in (_cdef.get("properties") or []):
                                        if _pp.get("name") == _pl and _pp.get("label"):
                                            _pl = _pp["label"]
                                            break
                            except Exception:
                                pass
                            _vt["label"] = f"{_cl}.{_pl}" if _pl else _cl
                        p["verify_target"] = _vt
            yield ('change_plans', _json.dumps(_plans, ensure_ascii=False))
            logger.info(f"[DynamicPlanner] LLM 推导 {len(_plans)} 个变更方案，{sum(1 for p in _plans if p.get('chain_id'))} 个已匹配链")

            # ── emit 事件: plan.generated ──
            try:
                from app.services.event_bus import event_bus
                missing = sum(
                    1 for p in _plans
                    if p.get("missing_actions") and len(p["missing_actions"]) > 0
                )
                missing_actions_list = []
                for p in _plans:
                    if p.get("missing_actions"):
                        missing_actions_list.extend(p["missing_actions"])
                await event_bus.publish("plan.generated", {
                    "conversation_id": session_id,
                    "conversation_owner": context.get("_user_id", ""),
                    "plan_count": len(_plans),
                    "plan_label": _plans[0].get("label", "") if _plans else "",
                    "matched_count": sum(1 for p in _plans if p.get("chain_id")),
                    "missing_actions_count": missing,
                    "missing_actions_list": ", ".join(missing_actions_list[:5]),
                })
                # 同时入 event_queue 持久化
                import json as _json2

                from app.db import get_db
                from app.models.event import EventQueue
                async for sess in get_db():
                    eq = EventQueue(
                        type="plan.generated",
                        payload=_json2.dumps({
                            "conversation_id": session_id,
                            "conversation_owner": context.get("_user_id", ""),
                            "plan_count": len(_plans),
                            "plan_label": _plans[0].get("label", "") if _plans else "",
                            "matched_count": sum(1 for p in _plans if p.get("chain_id")),
                            "missing_actions_count": missing,
                            "missing_actions_list": ", ".join(missing_actions_list[:5]),
                        }),
                    )
                    sess.add(eq)
                    await sess.commit()
                    break
            except Exception as e:
                logger.warning(f"[DynamicPlanner] emit plan.generated 失败: {e}")

    async def _llm_classify_intent(
        self, message: str, session_id: str, model_name: Optional[str],
    ) -> str:
        """独立 LLM 意图分类（规则未命中时的语义兜底）。

        输出 analysis | plan | execute。规则兜底命中的消息不会走到这里，
        因此这是对模糊表达（无明确"方案/改/删/查"等词）的兜底判断。
        """
        from app.services.llm_service import llm_service

        prompt = (
            "判断用户消息的意图，只输出一个 JSON 对象，不要输出任何其他文字。\n"
            '输出格式：{"intent":"<analysis|plan|execute>"}\n\n'
            "三档含义：\n"
            "- analysis：只查询/统计/分析/了解/评估/对比/报告结论，不涉及改动，也不要方案。\n"
            "- plan：要变更方案/建议/怎么改/如何调整，但没要求直接执行。\n"
            "- execute：明确要求执行改动（修改/删除/新增/替换/关闭/冻结/调整等）。\n\n"
            f"用户消息：{message}\n"
        )
        try:
            self._llm_calls += 1  # 预算计数：意图分类
            raw = await llm_service.chat_sync(
                message=prompt, session_id=session_id,
                system_prompt="你是意图分类器，严格只输出一个 JSON 对象，不要任何多余文字。",
                model_name=model_name or _get_configured_model("summary_model"),
            )
            raw = (raw or "").strip()
            # 优先解析 JSON 对象；失败则退化为匹配三档关键词
            _m = re.search(r'\{[^{}]*"intent"[^{}]*\}', raw, re.DOTALL)
            if _m:
                _data = json.loads(_m.group(0))
                _v = str(_data.get("intent", "")).strip().lower()
                if _v in ("analysis", "plan", "execute"):
                    return _v
            _m2 = re.search(r'\b(analysis|plan|execute)\b', raw)
            if _m2:
                return _m2.group(1)
        except Exception as e:
            logger.warning(f"[DynamicPlanner] LLM 意图分类失败，回退 analysis: {e}")
        return "analysis"  # 保守回退：不确定就不出方案，避免硬编

    async def _extract_params(
        self, message: str, concept: str,
        context: dict = None, steps_taken: list = None,
    ) -> dict:
        """从消息中提取查询参数，自动注入跨概念 join key。

        优先级：
        1. 前序步骤的已查询概念 → 实体解析 → join key
        2. 遍历所有上游关联概念 → 实体解析 → join key（LLM 可能跳过前序概念）
        3. 直接匹配：消息中的编码值 → 当前概念参数
        """
        params = {}
        from app.services.action_executor import action_executor
        action_executor._ensure_loaded()

        # 查询参数：优先用 query action 的显式查询参数（概念建模声明的查询入口），
        # 避免编译 skill 的全部输入（主键 + 所有 ref 属性）被 LLM 误填为查询条件
        # （如工单号 MO001 被填到 name/productionOrderId 主键/内部 id 字段）。
        # 回退到编译运行时 skill，再回退 action_executor 签名。
        sig_params = []
        query_sig = action_executor._sigs.get(f"{concept}_query", {})
        if query_sig and query_sig.get("params"):
            sig_params = query_sig.get("params", [])
        if not sig_params:
            skill = self._concept_skill_map.get(concept)
            if skill and skill.input_params:
                sig_params = [
                    {
                        "name": p.name,
                        "label": p.label,
                        "type": p.type,
                        "required": p.required,
                        "conceptPropertyRef": p.conceptPropertyRef,
                    }
                    for p in skill.input_params
                ]
        if not sig_params:
            sig = action_executor._sigs.get(f"{concept}_query", {})
            sig_params = sig.get("params", [])

        # 0. LLM 填槽（function calling 风格）：按 schema 从消息提取结构化参数。
        #    若成功，直接作为查询参数（如 ECNItem 查询 → ecnCode=ECN2026-002）；
        #    后续 join key 注入（确定性规则）在 params 非空时提前返回，用 LLM 结果。
        llm_params = await self._llm_extract_params(message, concept, sig_params)
        # 注意：不在此处立即 update 进 params——LLM 填槽可能编造消息中不存在的值
        # （如「设备DEMO-E-027有哪些手册」被幻觉填 equipment_model_id=27，消息里根本没有 27）。
        # 若先入 params，下方 join key 注入（确定性规则）的 `if params: return params` 会
        # 提前返回幻觉值、短路确定性的跨概念值（149）。故延后到方式 2/3 之后合并，
        # 确定性注入命中时优先，LLM 填槽仅在确定性注入均未命中时生效。

        # 1. 提取消息中的编码/数字（不用 \\b，中文也是 \\w 会导致边界匹配失败）
        # 支持连字符编号段：ECN2026-002 / MO002-RE-1（旧 regex 只提取 ECN2026，丢 -002 导致实体解析失败）
        # 以及「字母-字母-数字」格式：DEMO-E-027 / DEMO-MAN-027（此前不匹配 → 查询无过滤 → 返回首行错误数据）
        codes = re.findall(r'([A-Z]{2,8}(?:\d{2,8}(?:[-_][A-Za-z0-9]+)*|(?:[-_][A-Za-z0-9]+)+))', message)
        nums = re.findall(r'(?<![a-zA-Z])(\d{4,})(?![a-zA-Z])', message)
        all_values = codes + nums
        logger.info(
            f"[DynamicPlanner] _extract_params concept={concept} "
            f"codes={codes} nums={nums} steps_prev={len(steps_taken or [])}"
        )

        # 2. 跨概念自动注入 join key（消息编码 resolve 上游，兜底）
        # 遍历语义驱动（本体 traversal 声明，不写死概念名）：当前概念若是前序某概念的
        # traversal=recursive 出边 target（如子件物料是 BOM分录 的 recursive 子层），
        # 应取该前序结果的 join 值（方式 3），而非消息编码 resolve 更早父层的值
        # （方式 2 会把父层编码错配给子层）。
        _is_recursive_child = False
        for _ps in (steps_taken or []):
            _odef = action_executor._concepts.get(_ps.get("concept", ""), {})
            for _r in _odef.get("relations", []):
                if _r.get("target") == concept and (_r.get("traversal") or "one_hop") == "recursive":
                    _is_recursive_child = True
                    break
            if _is_recursive_child:
                break
        if all_values and not _is_recursive_child:
            from app.services.neo4j_service import neo4j_service

            # 确定要尝试的上游概念列表
            upstream_candidates = []
            if steps_taken:
                # 方式 A：从已查询的前序步骤
                for prev_step in reversed(steps_taken):
                    pc = prev_step.get("concept", "")
                    if pc and pc != concept:
                        upstream_candidates.append(pc)
            # 方式 B：遍历所有与当前概念有关联的上游概念（防 LLM 跳过前序概念）
            for skill in self.runtime.skills:
                sc = skill.concept
                if sc == concept or sc in upstream_candidates:
                    continue
                jk, _ = self._find_join_keys(sc, concept, allow_same_field=False)
                if jk:
                    upstream_candidates.append(sc)
            for upstream_concept in upstream_candidates:
                join_key, target_key = self._find_join_keys(upstream_concept, concept, allow_same_field=False)
                if not join_key:
                    continue

                for val in all_values:
                    entity = None
                    try:
                        upstream_def = action_executor._concepts.get(upstream_concept, {})
                        props = upstream_def.get("properties", [])
                        upstream_pk = "id"
                        for pp in props:
                            if pp.get("isPrimary"):
                                upstream_pk = pp["name"]
                                break
                        ns = upstream_def.get("namespace", "")
                        ns_where = " AND n._namespace = $ns" if ns else ""
                        # 匹配字段候选：仅主键 + 明确「编码/编号/外键」语义字段。
                        # 不再把"所有有 DB 映射的 string 字段"都当候选（如 remark/plantCode/voucherDate），
                        # 否则每个上游概念×每个 string 字段都发一次 0 行查询，导致海量无效 Neo4j 往返。
                        match_fields = [upstream_pk]
                        for pp in props:
                            nm = pp.get("name", "")
                            if nm == upstream_pk or nm in match_fields:
                                continue
                            is_ref = (pp.get("type") == "ref") or bool(pp.get("refConcept"))
                            is_code = bool(re.search(r'(code|_no$|编号|编码|单号|单号$)', nm, re.I))
                            if is_ref or is_code:
                                match_fields.append(nm)
                        entity = None
                        for mf in match_fields:
                            records = await neo4j_service.execute_read(
                                f"MATCH (n:{upstream_concept}) WHERE n.`{mf}` = $kw{ns_where} RETURN n LIMIT 1",
                                {"kw": val, "ns": ns},
                            )
                            if records:
                                entity = dict(records[0]["n"])
                                break
                    except Exception as exc:
                        logger.warning(f"[DynamicPlanner] resolve_entity({upstream_concept}, {val}) 异常: {exc}")
                        continue
                    if entity and entity.get(join_key) is not None:
                        join_value = entity[join_key]  # 保留原类型（整数等），避免 Neo4j 类型不匹配
                        # 依次按精确度匹配参数
                        for p in sig_params:
                            pname = p.get("name", "")
                            prop_ref = p.get("conceptPropertyRef", "")
                            if prop_ref and prop_ref == f"{upstream_concept}.{join_key}":
                                params[pname] = join_value
                                break
                        if not params:
                            # 依次优先 to 侧字段（target_key=EquipmentModel.id），
                            # 避免 from 侧同名（join_key=model_id）被签名顺序抢先误填
                            pname = None
                            for p in sig_params:
                                if p.get("name", "") == target_key:
                                    pname = target_key
                                    break
                            if pname is None and join_key and join_key != "id":
                                for p in sig_params:
                                    if p.get("name", "") == join_key:
                                        pname = join_key
                                        break
                            if pname is not None:
                                params[pname] = join_value
                        # 回退：匹配 conceptPropertyRef 引用上游概念的任意参数
                        if not params:
                            for p in sig_params:
                                pname = p.get("name", "")
                                prop_ref = p.get("conceptPropertyRef", "")
                                if prop_ref and prop_ref.startswith(upstream_concept + "."):
                                    params[pname] = join_value
                                    break
                        if params:
                            logger.info(
                                f"[DynamicPlanner] 自动注入: {upstream_concept}.{join_key}={join_value} → {concept}"
                            )
                            return params
                if params:
                    return params

        # 3. 从已查询的前序步骤结果提取 join key 值（遍历所有前序，不再只看上一跳，
        #    防止 LLM 规划顺序与本体关系图不一致时断链——如 BOM 分录→库存经 materialId 间接关联）
        if not params and steps_taken and context:
            for prev_step in reversed(steps_taken):
                prev_concept = prev_step.get("concept", "")
                if not prev_concept or prev_concept == concept:
                    continue
                prev_records = context.get(f"{prev_concept}_records", [])
                if not prev_records:
                    continue
                join_key, target_key = self._find_join_keys(prev_concept, concept)
                if not join_key:
                    continue
                # 提取前序结果中的 join key 值
                join_values = []
                seen = set()
                for rec in prev_records:
                    val = rec.get(join_key)
                    if val is not None and str(val) not in seen:
                        seen.add(str(val))
                        join_values.append(val)
                        if len(join_values) >= 50:
                            break
                if not join_values:
                    continue
                # 匹配参数：join 值应填到 to 侧外键字段（target_key），而非 from 侧主键字段
                pname = None
                for p in sig_params:
                    if p.get("name", "") == target_key:
                        pname = target_key
                        break
                if pname is None and join_key and join_key != "id":
                    for p in sig_params:
                        if p.get("name", "") == join_key:
                            pname = join_key
                            break
                if pname is not None:
                    params[pname] = join_values[0]
                if not params:
                    for p in sig_params:
                        pname = p.get("name", "")
                        prop_ref = p.get("conceptPropertyRef", "")
                        if prop_ref and prop_ref.startswith(prev_concept + "."):
                            params[pname] = join_values[0]
                            break
                if params:
                    logger.info(
                        f"[DynamicPlanner] 上一跳注入: {prev_concept}.{join_key}={join_values[:3]} → {concept}"
                    )
                    return params

        # 3.5 合并 LLM 填槽结果：仅在方式 2/3 确定性注入均未命中时生效
        #     （此时 LLM 填槽是唯一来源，如 ECNItem 查询 → ecnCode=ECN2026-002）。
        #     方式 2/3 已命中则已提前 return，不会走到这里。
        if llm_params:
            # 确定性校验：LLM 填槽的值必须字面出现在用户消息里，否则视为幻觉丢弃
            # （如把 SO-2026-001 填到 plantCode 工厂编码，或编造消息里不存在的值）。
            for _k, _v in llm_params.items():
                _sv = str(_v)
                if _sv in message:
                    params[_k] = _v
                else:
                    logger.warning(f"[DynamicPlanner] 丢弃 LLM 幻觉参数 {_k}={_sv}（消息中未字面出现）")

        # 4. 回退：直接匹配当前概念的查询参数（仅在 LLM 填槽/join 注入均未产出时）。
        #    优先字符串类型的业务编码字段，避免把字母编号（如 DEMO-E-027）填到整数主键 id
        #    导致查询过滤失败；且不得覆盖上面已解析出的参数。
        if not params and all_values:
            _target = None
            for p in sig_params:
                if p.get("type", "string") in ("string",) and p.get("name") != "id":
                    _target = p
                    break
            if _target is None and sig_params:
                _target = sig_params[0]
            if _target is not None:
                params[_target["name"]] = all_values[0]

        return params

    async def _llm_extract_params(self, message: str, concept: str, sig_params: list) -> dict:
        """LLM 填槽（function calling 风格）：按参数 schema 从消息提取结构化参数。

        与 base.py 单 action 路径的 extract_params_llm 同机制，供 DynamicPlanner 每步
        查询使用——从消息提取当前概念的查询参数（如 ECNItem 查询 → ecnCode=ECN2026-002），
        避免 regex 对带连字符编号（ECN2026-002）提取不完整。
        失败/无参数返回 {}，调用方回退 regex + join key 注入。
        """
        if not sig_params:
            return {}
        self._llm_calls += 1  # 预算计数：参数填槽
        try:
            from app.services.llm_service import llm_service
            schema_lines = []
            for p in sig_params:
                line = f"- {p['name']}（label={p.get('label', '')}, type={p.get('type', 'string')}"
                if p.get('required'):
                    line += ", 必填"
                ev = p.get('enumValues')
                if ev:
                    line += f", 枚举={list(ev)[:10]}"
                line += ")"
                schema_lines.append(line)
            prompt = (
                "从用户消息中提取查询参数值，只输出 JSON 对象。\n"
                f"参数 schema：\n{chr(10).join(schema_lines)}\n\n"
                "规则：\n"
                "- 只提取消息中明确出现的值，不要猜测、不要编造\n"
                "- **值必须字面出现在消息里**，且与参数的 label 语义匹配（如编号→编号参数、编码→编码参数、工厂→工厂参数），严禁把某个值填到语义不符的参数（如把订单号填到工厂编码）\n"
                "- 编码类值（如 ECN2026-002、MO001）填到对应的编码/编号参数\n"
                "- 无法提取的参数省略，不要输出空字符串\n"
                "- **消息含「所有/列表/全部/全量/列出/所有记录」等表示全量查询的词时，输出空对象 {}，不提取任何参数**\n"
                "- **消息含「其它/别的/其他/还有/另外」等指代词时，表示查询的是「除上文已提及对象之外的其余对象」，不是某个具体值，输出空对象 {}，严禁把上文出现过的编码（如 P001）当作参数值**\n"
                f"用户消息：{message}\n\n"
                '输出格式：{"参数名": "值"}，如 {"ecnCode": "ECN2026-002"}；全量/指代查询输出 {}'
            )
            model = _get_configured_model("decision_model")
            _skill = self._concept_skill_map.get(concept)
            _concept_cn = (getattr(_skill, "display_name", "") or getattr(_skill, "concept_label", "") or concept)
            async with span("dynamic_extract", "generic", concept=_concept_cn):
                raw = await asyncio.wait_for(
                    llm_service.chat_sync(
                        message=prompt,
                        system_prompt="你是精确的查询参数提取器，只输出 JSON，不输出任何解释。",
                        model_name=model,
                    ),
                    timeout=8.0,
                )
            raw = raw.strip()
            if raw.startswith("```"):
                lines = raw.split("\n")
                raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:]).strip()
            parsed = json.loads(raw)
            valid = {p['name'] for p in sig_params}
            out = {}
            for k, v in parsed.items():
                if k in valid and v is not None and str(v).strip() and k not in ('_fuzzy',):
                    out[k] = v if isinstance(v, (int, float)) else str(v).strip()
            if out:
                logger.info(f"[DynamicPlanner] LLM 填槽 {concept}: {out}")
            return out
        except asyncio.TimeoutError:
            logger.warning(f"[DynamicPlanner] LLM 填槽超时，回退 regex: {concept}")
        except Exception as e:
            logger.warning(f"[DynamicPlanner] LLM 填槽失败，回退 regex: {concept} {e}")
        return {}

    def _find_join_keys(self, from_concept: str, to_concept: str, allow_same_field: bool = True) -> tuple:
        """查找两个概念间的 join key。返回 (from_side_key, to_side_key) 或 (None, None)。

        allow_same_field=False 时只认「直接 relation.joinOn」，不认同名 ref 字段——
        跨概念注入（方式 2）用消息编码解析上游，同名 ref 字段易把「物料编码」误注入到
        「BOM 分录.materialId」，抢在正确的「BOM头.fid → BOM分录.bomId」之前命中，导致查错。
        """
        from app.services.action_executor import action_executor
        action_executor._ensure_loaded()

        from_def = action_executor._concepts.get(from_concept, {})
        # 正向: from → to
        for rel in from_def.get("relations", []):
            if rel.get("target") == to_concept and rel.get("joinOn"):
                keys = self._parse_join_on(rel["joinOn"], from_concept, to_concept)
                if keys[0]:
                    return keys
        # 反向: to → from
        to_def = action_executor._concepts.get(to_concept, {})
        for rel in to_def.get("relations", []):
            if rel.get("target") == from_concept and rel.get("joinOn"):
                keys = self._parse_join_on(rel["joinOn"], from_concept, to_concept)
                if keys[0]:
                    return keys
        if not allow_same_field:
            return (None, None)
        # 同名字段 join（通用兜底，不写死概念名）：两概念有同名外键字段（如 materialId/bomId/saleOrderId/billId），
        # 且该字段非主键、非纯 id，且 refConcept 一致 → 视为可 join。
        # 覆盖「BOM 分录 → 库存」这类经同一物料（materialId）间接关联、但无直接 relation 的链路。
        from_props = {p.get("name"): p for p in from_def.get("properties", [])}
        to_props = {p.get("name"): p for p in to_def.get("properties", [])}
        for _name, _fp in from_props.items():
            if _name in ("id",) or _fp.get("isPrimary"):
                continue
            _tp = to_props.get(_name)
            if not _tp or _tp.get("isPrimary"):
                continue
            _same_ref = bool(_fp.get("refConcept")) and _fp.get("refConcept") == _tp.get("refConcept")
            if _same_ref:
                return (_name, _name)
        return (None, None)

    @staticmethod
    def _parse_join_on(join_on: str, from_concept: str, to_concept: str) -> tuple:
        """解析 joinOn 字符串，提取 from/to 两侧的属性名。"""
        from_key, to_key = None, None
        for part in join_on.split("="):
            part = part.strip()
            if part.startswith(from_concept + "."):
                from_key = part.split(".")[1].strip()
            elif part.startswith(to_concept + "."):
                to_key = part.split(".")[1].strip()
        return (from_key, to_key)


# ── 参数提取 — 从查询数据中自动提取关键参数值 ─────────────────

def _sanitize_verify_target(vt: dict) -> Optional[dict]:
    """生成后校验 LLM 产出的 verify_target，纠正幻觉字段/枚举，不合法则返回 None。

    校验并纠正：
    1. concept 必须是本体真实概念名（用概念名/label 反查）。
    2. property 必须是该概念的属性名（用属性名/label 反查），不存在则返回 None。
    3. filters 键必须是该概念的属性名，幻觉字段（如 WorkOrder 上的 workOrderCode）剔除。
    4. expected 若属性是枚举：填中文显示值则保留；填了不存在的值（如「已关闭」）
       无法映射到合法枚举 → 返回 None（宁可验证失败暴露，不静默错判）。

    返回纠正后的 vt（dict）或 None（verify_target 不可用，应丢弃）。
    """
    if not isinstance(vt, dict) or not vt.get("concept"):
        return None
    try:
        from app.services.ontology_service import ontology_service
        ontology_service._ensure_fresh()
    except Exception:
        return None

    # 1. concept 真实性校验（支持 label 反查）
    concept = str(vt.get("concept", "") or "").strip()
    concept_def = ontology_service.get_concept(concept)
    if not concept_def:
        # 用中文 label 反查概念名
        for c in ontology_service.get_concepts():
            if (c.get("label") or "").strip() == concept:
                concept = c.get("name", "")
                concept_def = c
                break
    if not concept_def:
        logger.warning(f"[DynamicPlanner] verify_target 概念不存在或无法解析: {vt.get('concept')}")
        return None
    props = concept_def.get("properties") or []
    prop_by_name = {p.get("name"): p for p in props}
    prop_by_label = {str(p.get("label", "")).strip(): p for p in props if p.get("label")}

    # 2. property 真实性校验（支持 label 反查）
    prop = str(vt.get("property", "") or "").strip()
    if prop and prop not in prop_by_name and prop in prop_by_label:
        prop = prop_by_label[prop].get("name", "")
    if not prop or prop not in prop_by_name:
        logger.warning(f"[DynamicPlanner] verify_target 属性不存在 {concept}.{prop or vt.get('property')}，丢弃验证目标")
        return None

    # 3. filters 键真实性校验：剔除幻觉字段
    filters = vt.get("filters") or {}
    if isinstance(filters, dict):
        clean_filters = {}
        for k, v in filters.items():
            if k in prop_by_name:
                clean_filters[k] = v
            elif k in prop_by_label:
                clean_filters[prop_by_label[k].get("name", "")] = v
            else:
                logger.warning(f"[DynamicPlanner] verify_target 定位字段不存在 {concept}.{k}，已剔除")
        filters = clean_filters

    # 4. expected 枚举合法性校验
    expected = vt.get("expected")
    prop_def = prop_by_name.get(prop, {})
    ev = prop_def.get("enumValues")
    if isinstance(ev, str):
        try:
            ev = json.loads(ev)
        except (json.JSONDecodeError, TypeError):
            ev = None
    if isinstance(ev, dict) and ev:
        exp = str(expected if expected is not None else "").strip()
        valid_codes = {str(k) for k in ev.keys()}
        valid_labels = {str(v) for v in ev.values()}
        if exp and exp not in valid_codes and exp not in valid_labels:
            logger.warning(f"[DynamicPlanner] verify_target 期望值不在枚举 {concept}.{prop}={exp}，合法值={valid_labels}，丢弃验证目标")
            return None

    out = dict(vt)
    out["concept"] = concept
    out["property"] = prop
    if filters:
        out["filters"] = filters
    elif "filters" in out:
        del out["filters"]
    return out


def _extract_params_from_context(context: dict, steps_taken: list) -> dict:
    """从分析查询结果中提取关键参数，用中文键名，供前端展示和 {{plan.xxx}} 引用。

    优先从结构化 records 提取，回退到 markdown 表格文本解析。
    """
    params = {}
    import re as _re2

    # 候选映射：概念属性名 → 提取为 plan 参数（用本体属性名，与 action_params 直接对齐）
    PARAM_MAP = {
        "code": "code", "materialCode": "materialCode", "materialName": "materialName",
        "quantity": "quantity", "routingCode": "routingCode", "endDate": "endDate",
    }

    for key, value in context.items():
        if not key.endswith("_records") or not value:
            continue
        records = value if isinstance(value, list) else []
        if not records:
            continue
        first = records[0] if isinstance(records[0], dict) else {}
        for prop, label in PARAM_MAP.items():
            if label in params:
                continue
            val = first.get(prop)
            if val is not None and str(val).strip() and str(val).strip() != "-":
                params[label] = str(val).strip()

    # 回退：从 _result 文本中提取（records 为空时）
    if not params:
        for key, value in context.items():
            if not key.endswith("_result") or not value:
                continue
            text = str(value)
            code_match = _re2.search(r'(?:工单号|编码|code)\s*\|\s*([^\|\n]+)', text)
            if code_match and "工单号" not in params:
                code_val = code_match.group(1).strip()
                if code_val and code_val != "-":
                    params["工单号"] = code_val
            mat_match = _re2.search(r'(?:物料编码|materialCode)\s*\|\s*([^\|\n]+)', text)
            if mat_match and "物料编码" not in params:
                mat_val = mat_match.group(1).strip()
                if mat_val and mat_val != "-":
                    params["物料编码"] = mat_val
    return params


def _action_label(action_name: str) -> str:
    """Action 名 → 中文标签（从签名读，不硬编码）。"""
    if action_name == "query":
        return "查询"
    from app.services.action_executor import action_executor as _ae
    _ae._ensure_loaded()
    fn = _ae._sigs
    # 尝试多种匹配: conceptName_actionName
    for sig in fn.values():
        if sig.get("actionName") == action_name:
            return f"的{sig.get('actionLabel', action_name)}"
    return f"的{action_name}"


# ── 方案把关：查询/核实类退化方案过滤 ─────────────────────

_QUERY_VERBS = ("查询", "核实", "确认", "检查", "查看", "评估", "对比", "分析", "判断", "验证", "了解", "审查", "核对")


def _is_degenerate_plan(plan: dict) -> bool:
    """判断是否为"查询/核实类"退化方案：无实际变更动作，且步骤全是查询/核实动词。

    缺数据时 LLM 常把"查一下 X 确认一下 Y"硬编成方案——这不是可执行的变更方案，
    应过滤掉，改由报告正文表达。有 actions（真实变更动作）的方案不过滤。
    """
    actions = plan.get("actions") or []
    if actions:
        return False
    steps = plan.get("steps_preview") or []
    if not steps:
        return True
    return all(any(v in str(s) for v in _QUERY_VERBS) for s in steps)


# ── 意图分类：规则兜底（确定性优先，命中即定档，不走 LLM） ──

# 优先级：plan > execute > analysis（"方案/怎么改"这类要方案的词优先于 execute 动词单字，避免误判）
_PLAN_WORDS = (
    '方案', '计划', '怎么改', '如何改', '怎么调整', '如何调整', '怎样调整',
    '如何处置', '怎么处理', '如何变更', '怎么变更', '怎么变', '如何变',
    '给个建议', '建议方案', '应该怎么改', '应该怎样改',
)
_EXECUTE_WORDS = (
    '执行', '帮我改', '直接改', '直接删', '直接加', '直接换', '直接关', '直接冻',
    '修改', '删除', '新增', '替换', '关闭', '冻结', '暂停',
    '退库', '返工', '重建', '冲销', '回退', '复制', '初始化', '更新',
)
_ANALYSIS_WORDS = (
    '查询', '查看', '统计', '了解', '报告', '评估', '对比', '分析', '判断',
    '验证', '怎么样', '会怎样', '为什么', '是多少', '是什么', '有哪些', '影响',
)


def _classify_change_intent(msg: str) -> Optional[str]:
    """规则兜底：确定性判定用户意图。

    返回 'plan' | 'execute' | 'analysis' | None。
    None 表示规则未命中（高置信词未出现），交由独立 LLM 分类兜底。

    优先级：plan > execute > analysis。命中"方案/怎么改"这类要方案的词直接定 plan，
    避免被 execute 里的动词单字抢先误判。
    """
    if not msg:
        return None
    if any(w in msg for w in _PLAN_WORDS):
        return 'plan'
    if any(w in msg for w in _EXECUTE_WORDS):
        return 'execute'
    if any(w in msg for w in _ANALYSIS_WORDS):
        return 'analysis'
    return None


# ── 链自动匹配 ──────────────────────────────────────────────

async def _match_chains_to_plans(plans: list) -> list:
    """为 LLM 生成的方案匹配已有的 pipeline 链。

    匹配逻辑：LLM 推荐的 actions 集合 ∩ 链表步骤的 action_name 集合 → 最高重合度匹配。
    无匹配链时 chain_id 为空，前端出「配置执行链」引导。
    """
    if not plans:
        return []

    # 收集所有方案推荐的 Action
    plan_actions = {}
    for p in plans:
        actions = set(p.get("actions", []))
        if not actions:
            # LLM 可能没输出 actions，尝试从 steps_preview 提取
            actions = set()
        plan_actions[p.get("id", "")] = actions

    # 从 DB 加载所有 pipeline 链（按当前本体图谱 namespace 过滤）
    try:
        from app.db import _async_session as _sf
        from app.repositories.chain_repo import ChainRepository
        from app.services.ontology_service import ontology_service

        chains = []
        async with _sf() as session:
            repo = ChainRepository(session)
            all_chains = await repo.list_all(ontology_service.active_namespace or "")
            chains = [c for c in all_chains if c.mode == "pipeline" and c.enabled]

        for plan in plans:
            plan_id = plan.get("id", "")
            plan_act_set = plan_actions.get(plan_id, set())

            chain_scores = []
            for chain in chains:
                chain_actions = {s.action_name for s in chain.steps if s.action_name}
                if not chain_actions:
                    continue
                overlap = chain_actions & plan_act_set
                if overlap:
                    score = len(overlap) / len(chain_actions)
                    chain_scores.append({
                        "chain_id": chain.chain_id,
                        "score": score,
                        "overlap": list(overlap),
                    })

            if chain_scores:
                best = max(chain_scores, key=lambda c: c["score"])
                plan["chain_id"] = best["chain_id"]
                plan["match_score"] = round(best["score"], 2)
                # 用链的实际步骤名称替换 LLM 生成的通用描述
                matched_chain = next((c for c in chains if c.chain_id == best["chain_id"]), None)
                if matched_chain:
                    plan["steps_preview"] = [s.description or s.step_id for s in matched_chain.steps]
                    plan["chain_name"] = matched_chain.name or ""
            else:
                plan["chain_id"] = ""
    except Exception as e:
        logger.warning(f"[DynamicPlanner] 链匹配失败: {e}")
        for p in plans:
            p.setdefault("chain_id", "")

    # ── 检查方案引用的 action 是否存在 ──
    try:
        from app.services.action_executor import action_executor
        action_executor._ensure_loaded()
        all_actions = set(action_executor._sigs.keys())
        for plan in plans:
            plan_actions_list = plan.get("actions", [])
            existing = [a for a in plan_actions_list if a in all_actions]
            missing = [a for a in plan_actions_list if a not in all_actions]
            plan["existing_actions"] = existing
            plan["missing_actions"] = missing
    except Exception as e:
        logger.warning(f"[DynamicPlanner] action 存在性检查失败: {e}")
        for p in plans:
            p.setdefault("existing_actions", [])
            p.setdefault("missing_actions", [])

    return plans
