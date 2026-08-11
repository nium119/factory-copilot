"""动态多跳查询规划器 — ReAct 风格 LLM 决策。

查询统一走 action executor，不做二次降级。
"""
import asyncio
import json
import os
import re
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional, AsyncGenerator


def _get_configured_model(key: str) -> str:
    """从全局配置读取模型"""
    from app.agents.settings.model import MODEL_CONFIG
    return MODEL_CONFIG.get(key)

from loguru import logger


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

    MAX_STEPS = 6

    def __init__(self, runtime: CompiledRuntime):
        self.runtime = runtime
        self._skill_map = {s.name: s for s in runtime.skills}
        self._concept_skill_map = {s.concept: s for s in runtime.skills}
        self._mcp_tools: dict = {}  # P3：外部 MCP 工具 {name: sig}，loop 可自主调度

    def build_planner_prompt(self) -> str:
        """构建注入给 LLM 的规划上下文。"""
        parts = [
            "你是制造业智能分析助手。你可以查询以下概念的数据：",
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

        parts.append("## 分析规则")
        parts.append("1. 一次只查询一个概念")
        parts.append(f"2. 根据查询结果中的关联数据决定下一步，最多 {self.MAX_STEPS} 步")
        parts.append("3. 查询完成后输出汇总结论 + P0/P1/P2 行动项")
        parts.append("4. 无数据时如实告知，不编造")
        parts.append("5. 单维度不确定（如仅缺时间）→ 用默认值（如本月）。多维度不确定（缺概念+缺时间）→ ASK分组确认。")
        parts.append("6. 当前消息简短且有对话历史时，是追问回复，提取历史中的完整意图直接执行，不要再次反问。")
        parts.append("7. 始终先查用户直接指定的概念（如工单），再查关联概念。用上一跳结果的ID/编号值做过滤。例如：先查WorkOrder获取id=990，再查WorkOrderBOM带上workOrderCode=990。禁止无过滤条件查全表。")
        parts.append("8. 变更/工程变更影响分析（含'影响/后果/涉及/影响哪些'）→ 依次查询完整链路：")
        parts.append("   变更通知 → 变更明细 → 物料替换 → 库存影响 → 关联工单/BOM")
        parts.append("   用上一跳的编码值过滤下一跳（如 ecnCode=ECN2026-002），查满完整链路后再汇总，不要提前汇总。")
        parts.append("")
        parts.append("## 根因分析规则（仅问题含为什么/异常/故障/延期/根因时生效）")
        parts.append("- 先查直接对象 → 结果含异常标记(❌/挂起/失败)时 → 沿关系逆流追溯上游")
        parts.append("- 追溯链: 直接对象 → 关联工序/任务 → 关联设备/物料 → 维保/人员")
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
                '{"steps": [{"concept": "概念名", "reason": "查询理由"}, ...], "ask": null}\n'
                "规则：\n"
                f"- 根据用户消息一次规划完整的多步查询步骤序列，最多 {self.MAX_STEPS} 步\n"
                "- 概念名必须来自上面可查询的概念；用上一跳结果值过滤下一跳\n"
                "- 变更影响分析须查完整链路（变更通知→明细→替换→库存影响→工单/BOM）\n"
                '- 用户要"相似/找相似"时，步骤加 "type": "find_similar" 和 "target": "目标标识"\n'
                "- 用户消息已含明确对象/编码（如 ECN2026-002、MO001）或明确分析意图（变更/影响/分析/库存/工单）时，必须直接规划，禁止 ask\n"
                '- 仅当消息完全没有业务对象和意图时才输出 ask：{"steps": [], "ask": "需要确认的问题"}'
            )
            prompt = planner + plan_instruction + f"\n## 用户消息\n{message}"
            model = _get_configured_model("decision_model")
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
                concept = concept_label_map.get(str(s.get("concept", "")).strip())
                if concept and (concept in self._concept_skill_map or concept in self._mcp_tools):
                    steps.append({
                        "concept": concept,
                        "reason": str(s.get("reason", ""))[:80],
                        "type": "find_similar" if str(s.get("type", "")) == "find_similar" else "query",
                        "target": str(s.get("target", "")).strip(),
                    })
            # 需求覆盖评审（LLM 语义，无硬编码映射）：看计划是否覆盖用户需求，缺失则补
            steps = await self._review_plan(message, steps)
            if steps:
                logger.info(f"[DynamicPlanner] 计划 {len(steps)} 步: {[s['concept'] for s in steps]}")
            return steps, ask

        except asyncio.TimeoutError:
            logger.warning("[DynamicPlanner] 计划超时，回退无计划")
        except Exception as e:
            logger.warning(f"[DynamicPlanner] 计划失败: {e}")
        return [], None

    async def _review_plan(self, message: str, steps: list) -> list:
        """需求覆盖评审（LLM 语义，无硬编码映射）：判断计划是否覆盖用户需求，缺失概念则补。

        输入消息 + 当前计划 + 可查询概念目录（build_planner_prompt 含 skill 目录 + 本体关系图），
        LLM 判断计划缺失的需求概念并补充——如"分析 ECN 对库存、生产影响"缺库存/工单概念则补。
        失败返回原 steps（不影响执行）。
        """
        if not steps:
            return steps
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

        # Phase 1: 计划——一次 LLM 输出完整步骤序列（先计划后执行，业界标准）。
        # 相比逐步骤 LLM 决策：计划一次定死、执行确定性，根治"每步随机/提前汇总"。
        steps, ask = await self._plan_steps(message, history_messages)
        if ask:
            yield ('content', f"\n\n---\n### 需要确认\n\n{ask}")
            yield ('done', json.dumps({"steps_taken": 0, "quick_replies": []}))
            return
        if not steps:
            yield ('error', "无法规划分析步骤，请补充信息")
            yield ('done', json.dumps({"steps_taken": 0, "max_steps": self.MAX_STEPS}))
            return

        summary_produced = False
        for step_num, step in enumerate(steps, 1):
            concept = step.get("concept", "")
            reason = step.get("reason", "")
            # 计划步骤类型：find_similar（相似匹配）或 query（默认查询）
            decision = {
                "action": "find_similar" if step.get("type") == "find_similar" else "query",
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
                yield ('content', f"\n\n---\n### 综合汇总\n\n")
                async for chunk_type, chunk_content in self._llm_summarize(
                    decision_prompt, context, model_name, enable_thinking, session_id, steps_taken,
                ):
                    yield (chunk_type, chunk_content)
                break

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
                query_ok = False
                tool_name = f"{concept}_query"
                sig = action_executor._sigs.get(tool_name)
                if not sig:
                    sig = {"conceptName": concept, "functionName": tool_name}
                from app.services.data_backend import data_backend as _db

                # 简单查询：确定性执行，空/失败直接接受，不每步 LLM 反思（反思聚焦汇总前整体评估）
                try:
                    params = await self._extract_params(message, concept, context, steps_taken)
                    result, row_count, _, raw_records = await action_executor._query_via_backend(
                        concept, sig, params, _db,
                    )
                    result = self._strip_internal_ids(result, concept)
                    context[f"{concept}_result"] = result
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

        # 汇总前整体反思：评估数据能否支撑回答；缺且可补查则补查，否则产出回答边界结论
        if steps_taken:
            _gr = await self._reflect_global(message, context, steps_taken)
            _boundary = (_gr.get("boundary") or "").strip()
            if _gr.get("need_more"):
                _extra = (_gr.get("concepts") or [None])[0]
                if _extra and (_extra in self._concept_skill_map or _extra in self._mcp_tools):
                    _ex_skill = self._concept_skill_map.get(_extra)
                    _ex_desc = getattr(_ex_skill, "display_name", "") or _extra
                    yield ('think', json.dumps({
                        "step": len(steps) + 1, "concept": "", "concept_label": "整体评估",
                        "content": f"整体反思：结果缺关键数据，补查{_ex_desc}",
                    }, ensure_ascii=False))
                    yield ('step', json.dumps({
                        "step": len(steps) + 1, "action": "query_start",
                        "concept": _extra,
                        "description": f"补查{_ex_desc}: {_gr_reason[:80]}",
                        "model": _get_configured_model("decision_model"),
                    }, ensure_ascii=False))
                    _eok = False
                    try:
                        from app.services.data_backend import data_backend as _edb
                        _ex_sig = action_executor._sigs.get(f"{_extra}_query") or {"conceptName": _extra, "functionName": f"{_extra}_query"}
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


        if not summary_produced and steps_taken:
            # 最后一步强制汇总
            yield ('step', json.dumps({
                "step": self.MAX_STEPS + 1, "action": "summary",
                "description": "综合汇总",
                "model": model_name or _get_configured_model("summary_model"),
            }, ensure_ascii=False))
            yield ('content', f"\n\n---\n### 综合汇总\n\n")
            async for chunk_type, chunk_content in self._llm_summarize(
                self._build_decision_prompt(
                    self.build_planner_prompt(), message, steps_taken, context, self.MAX_STEPS, history_messages,
                ), context, model_name, enable_thinking, session_id, steps_taken,
            ):
                yield (chunk_type, chunk_content)

        yield ('done', json.dumps({
            "steps_taken": len(steps_taken),
            "max_steps": self.MAX_STEPS,
        }, ensure_ascii=False))

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

        if steps:
            parts.append("## 已完成的查询")
            for s in steps:
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

    async def _llm_decide(
        self, prompt: str, model_name: Optional[str],
        enable_thinking: Optional[bool], session_id: str,
    ) -> dict:
        """LLM 决策: QUERY:concept 还是 SUMMARY:content。"""
        try:
            from app.services.llm_service import llm_service

            response = ""
            async with asyncio.timeout(30):
                async for chunk_type, chunk_content in llm_service.chat_stream(
                    message=prompt, session_id=session_id,
                    system_prompt=(
                        "你是简洁的决策引擎。\n"
                        "消息包含明确数据查询需求（编码/数字/分析词）→ QUERY。\n"
                        "消息明确要求匹配相似/找相似（如「匹配相似BOM」「找相似物料」）→ FIND_SIMILAR。\n"
                        "消息为闲聊/测试/问候/无数据需求 → SUMMARY 简短回复即可。\n"
                        "单维度不确定（缺时间）→用默认值QUERY。\n"
                        "多维度不确定（缺概念+缺时间）→ASK分组确认。\n"
                        "\n"
                        '示例：「你好」→ SUMMARY:你好！请描述你想查询的数据。\n'
                        '示例：「测试」→ SUMMARY:系统就绪，请描述你的数据查询需求。\n'
                        '示例：「最近情况」→ ASK:您想了解哪方面？|时间:今天,本周,本月|维度:生产进度,质量,设备状态\n'
                        '示例：「匹配和MO001相似的工单BOM」→ FIND_SIMILAR: WorkOrderBOM target=MO001 reason=用户想找相似BOM模板'
                    ),
                    model_name=model_name or _get_configured_model("decision_model"),
                    enable_thinking=False,
                    tools=None,
                ):
                    if chunk_type == 'content':
                        response += chunk_content

            response = response.strip()
            if response.startswith("ASK:") or response.startswith("ASK："):
                text = response.replace("ASK:", "").replace("ASK：", "").strip()
                # 解析多分组: "问题描述 | 组1:选项1,选项2 | 组2:选项3,选项4"
                groups = []
                reason = text
                if "|" in text:
                    parts = [p.strip() for p in text.split("|")]
                    reason = parts[0] if parts else text
                    for g in parts[1:]:
                        if ":" in g or "：" in g:
                            label, opts = g.split(":", 1) if ":" in g else g.split("：", 1)
                            groups.append({"label": label.strip(), "options": [o.strip() for o in opts.split(",") if o.strip()]})
                        else:
                            # 无标签的选项组
                            groups.append([o.strip() for o in g.split(",") if o.strip()])
                return {"action": "ask", "reason": reason, "groups": groups}
            elif response.startswith("SUMMARY:") or response.startswith("SUMMARY："):
                return {"action": "summary"}
            elif response.startswith("FIND_SIMILAR:") or response.startswith("FIND_SIMILAR："):
                text = response.replace("FIND_SIMILAR:", "").replace("FIND_SIMILAR：", "").strip()
                concept = text
                target = ""
                reason = ""
                # 解析: ConceptName target=xxx reason=yyy
                if " " in text:
                    parts = text.split(" ", 1)
                    concept = parts[0].strip()
                    rest = parts[1].strip() if len(parts) > 1 else ""
                    for kv in rest.split(" "):
                        if "=" in kv:
                            k, v = kv.split("=", 1)
                            if k.strip() == "target":
                                target = v.strip()
                            elif k.strip() == "reason":
                                reason = v.strip()
                concept = re.split(r'[\(（]', concept)[0].strip()
                resolved = self._resolve_concept(concept)
                logger.info(f"[DynamicPlanner] find_similar '{concept}' → '{resolved}' target={target}")
                return {"action": "find_similar", "concept": resolved, "targetKey": target, "reason": reason[:80]}
            elif response.startswith("QUERY:") or response.startswith("QUERY："):
                concept = response.replace("QUERY:", "").replace("QUERY：", "").strip()
                if " " in concept:
                    parts = concept.split(" ", 1)
                    concept = parts[0].strip()
                    reason = parts[1].strip() if len(parts) > 1 else ""
                else:
                    reason = ""
                # 剥离 LLM 附加的括号内容: "工单派工(WorkOrderDispatch)" → "工单派工"
                # LLM 也可能输出 "工单(原因, 10字以内)" → 取第一个左括号前的内容
                concept = re.split(r'[\(（]', concept)[0].strip()
                resolved = self._resolve_concept(concept)
                logger.info(f"[DynamicPlanner] resolved '{concept}' → '{resolved}'")
                return {"action": "query", "concept": resolved, "reason": reason[:80]}
            else:
                logger.info(f"[DynamicPlanner] 无法解析决策, 默认汇总: {response[:100]}")
                return {"action": "summary"}
        except Exception as e:
            logger.error(f"[DynamicPlanner] LLM 决策失败: {e}")
            return {"action": "summary"}

    async def _llm_summarize(
        self, decision_prompt: str, context: dict,
        model_name: Optional[str], enable_thinking: Optional[bool],
        session_id: str, steps_taken: list = None,
    ) -> AsyncGenerator[tuple, None]:
        """流式输出 LLM 汇总总结。"""
        from app.services.llm_service import llm_service

        data_text_parts = []
        for k, v in context.items():
            if k != "message" and v:
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
            f"注意：不同概念的属性值天然不同（如工单物料是成品料号，BOM物料是组件料号），非异常。"
            f"**相似匹配结果来自其他工单的历史BOM，仅用于模板参考，禁止将其判断为当前工单的「缺失物料」。**"
            f"BOM完整性检查必须基于该工单自身的BOM结构定义，不能以跨工单相似度作为漏项依据。"
            f"数据充分时分层报告（概览→发现→行动）；"
            f"数据不足时简洁总结 + P0/P1/P2 行动项，无数据直接告知。"
        )
        ops_list = "\n".join(
            f"- {s.display_name or s.concept_label}{_action_label(a)}（{s.concept}_{a}）"
            for s in self.runtime.skills
            for a in (s.actions if hasattr(s, 'actions') and s.actions else ['query'])
        )
        _ops_section = f"\n## 可用操作（优先选用，若需其他操作也可提出）\n{ops_list}\n"
        _change_section = (
            "\n## 变更方案输出要求"
            "\n**意图判定（最高优先级）**：若用户仅要求分析/查看/了解/统计/报告（无修改、创建、删除、修复、调整、更新、复制、初始化等操作意图），"
            "\n  则**绝对禁止输出变更方案 JSON**——即使发现数据问题（如BOM缺失、状态异常），也只在报告「行动建议」小节给出建议，不生成变更方案。"
            "\n  仅当用户明确要求执行/修改/修复/调整/新增/删除/复制/初始化等**操作**时，才输出变更方案。"
            "\n如果分析涉及变更操作（增/删/改/替换/调整），在报告末尾用 ```json 代码块输出变更方案数组："
            "\n[{\"id\":\"plan_1\",\"label\":\"方案标题\",\"recommended\":true,\"risk\":\"low|medium|high\","
            "\n  \"precondition\":\"前提条件\",\"impact\":\"影响说明\","
            "\n  \"steps_preview\":[\"步骤1\",\"步骤2\"],"
            "\n  \"actions\":[\"ConceptName_actionName\"],"
            "\n  \"action_labels\":[\"操作中文名\"],"
            "\n  \"params_suggestion\":{\"工单号\":\"MO001\",\"物料编码\":\"380000\"},"
            "\n  \"verify_target\":{\"concept\":\"WorkOrderBOM\",\"property\":\"quantity\",\"expected\":\"8\",\"label\":\"BOM拆分数\",\"filters\":{\"workOrderCode\":\"MO001\"}}}]"
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
            "\n  系统执行完变更后会自动复查并判定目标是否达成。仅当方案确实无法确定可复查的具体字段时省略 verify_target，并在报告说明原因。"
            "\n如无变更需求则不输出此 JSON 块。"
            "\n**相似匹配规则**：若分析涉及推荐相似实例作为模板，必须输出变更方案，actions 从上方可用操作中选择对应的新增/复制操作。"
            "\n## 报告输出规范"
            "\n### 0. 禁止推测补全（最高优先级，约束整个报告正文）"
            "\n- 报告中**严禁用通用知识、行业常识、推断、猜测补全查询不到的数据**。"
            "\n- 例如：工单BOM明细未查到，就**不得**写\"潜在组件物料/可能涉及的材料（PVC、光纤、护套料等）\"——这些不是查询结果。"
            "\n- 只能报告查询实际返回的事实；查不到的项在「🔍 数据缺失」小节如实说明，宁缺毋滥，不做知识性补全。"
            "\n### 1. 中文命名"
            "\n报告中**绝对禁止**出现英文概念名（如 WorkOrder、WorkOrderBOM）和英文属性名（如 materialCode、workOrderCode）。"
            "\n必须全部使用中文名称，例如：工单BOM、物料编码、工单号、计划数量、开工日期。"
            "\n概念名参考：「WorkOrder→工单」「WorkOrderBOM→工单BOM」「WorkOrderTask→工单任务」「WorkOrderDispatch→工单派工」"
            "\n### 2. 隐藏数据库ID"
            "\n- **禁止暴露数据库自增ID**（如 990、10079 等无业务含义的数字主键）。"
            "\n- 用业务编码替代：工单号（MO001）代替 id（990），物料编码（E34-053-0000-00）代替 name（10079）。"
            "\n- 表格列头只用中文标签，不用字段名。"
            "\n### 3. 排版格式"
            "\n- 关键数值用**粗体**突出"
            "\n- 状态用 ✅❌⚠️ 标记"
            "\n- 数据对比用 Markdown 表格"
            "\n- 发现和结论用 🔍📊⚠️ 等 emoji 分节"
            "\n- 行动建议用 P0🔴 / P1🟡 / P2🟢 标记优先级"
            "\n### 4. 禁用"
            "\n- 报告中绝对禁止出现英文操作名（如 WorkOrderBOM_findSimilar、adjustBomQty），"
            "\n  这些仅在 JSON 的 actions 字段中使用，正文里必须用中文（如\"匹配相似工单BOM\"\"调整BOM用量\"）"
            "\n- 正文中禁止出现英文概念名（WorkOrderBOM → 工单BOM）、英文属性名（materialCode → 物料编码）"
            "\n### 5. 数据缺失标注"
            "\n- 若关键概念查不到数据（如工单BOM明细为空、物料编码缺失、用量未知），"
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
        async for chunk_type, chunk_content in llm_service.chat_stream(
            message=summary_prompt, session_id=session_id,
            model_name=model_name or _get_configured_model("summary_model"),
            enable_thinking=enable_thinking,
            system_prompt=(
                f"你是制造业数据分析专家{('，'+domain_hint) if domain_hint else ''}。"
                "根据数据量自适应：数据多→分层详报，数据少→简洁总结。不编造。"
                "⚠️ 绝对禁止使用英文概念名和属性名——全部用中文！"
                "用表格、emoji、粗体让报告清晰易读。"
                + anomaly_sys
            ),
            tools=None,
        ):
            if chunk_type == 'content':
                full_response += str(chunk_content)
            if chunk_type == 'thinking':
                logger.info(f"[DynamicPlanner] 收到 thinking chunk, len={len(str(chunk_content))}")
            yield (chunk_type, chunk_content)

        # 解析 LLM 输出的 JSON（变更方案或行动项）
        import re as _re, json as _json
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
                # verify_target：LLM 缺中文 label 或输出纯英文字段路径（如 "WorkOrderBOMItem.name"）
                # 时，重建为中文 label（概念中文名.属性中文名），避免前端显示原始字段名
                _vt = p.get("verify_target")
                if _vt and isinstance(_vt, dict):
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
                from app.models.event import EventQueue
                from app.db import get_db
                import json as _json2
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
        if llm_params:
            params.update(llm_params)

        # 1. 提取消息中的编码/数字（不用 \\b，中文也是 \\w 会导致边界匹配失败）
        # 支持连字符编号段：ECN2026-002 / MO002-RE-1（旧 regex 只提取 ECN2026，丢 -002 导致实体解析失败）
        codes = re.findall(r'([A-Z]{2,6}\d{2,8}(?:[-_][A-Za-z0-9]+)*)', message)
        nums = re.findall(r'(?<![a-zA-Z])(\d{4,})(?![a-zA-Z])', message)
        all_values = codes + nums
        logger.info(
            f"[DynamicPlanner] _extract_params concept={concept} "
            f"codes={codes} nums={nums} steps_prev={len(steps_taken or [])}"
        )

        # 2. 跨概念自动注入 join key（优先，更精确）
        if all_values:
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
                jk, _ = self._find_join_keys(sc, concept)
                if jk:
                    upstream_candidates.append(sc)
            for upstream_concept in upstream_candidates:
                join_key, target_key = self._find_join_keys(upstream_concept, concept)
                if not join_key:
                    continue

                for val in all_values:
                    entity = None
                    try:
                        upstream_def = action_executor._concepts.get(upstream_concept, {})
                        upstream_pk = "id"
                        for pp in upstream_def.get("properties", []):
                            if pp.get("isPrimary"):
                                upstream_pk = pp["name"]
                                break
                        ns = upstream_def.get("namespace", "")
                        ns_where = " AND n._namespace = $ns" if ns else ""
                        records = await neo4j_service.execute_read(
                            f"MATCH (n:{upstream_concept}) WHERE n.`{upstream_pk}` = $kw{ns_where} RETURN n LIMIT 1",
                            {"kw": val, "ns": ns},
                        )
                        if records:
                            entity = dict(records[0]["n"])
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
                            for p in sig_params:
                                pname = p.get("name", "")
                                if pname in (target_key, join_key):
                                    params[pname] = join_value
                                    break
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

        # 3. 从上一跳查询结果提取 join key 值（第二跳及后续）
        if not params and steps_taken and context:
            prev_step = steps_taken[-1]
            prev_concept = prev_step.get("concept", "")
            if prev_concept and prev_concept != concept:
                prev_records = context.get(f"{prev_concept}_records", [])
                if prev_records:
                    join_key, target_key = self._find_join_keys(prev_concept, concept)
                    if join_key:
                        # 提取上一跳结果中的 join key 值
                        join_values = []
                        seen = set()
                        for rec in prev_records:
                            val = rec.get(join_key)
                            if val is not None and str(val) not in seen:
                                seen.add(str(val))
                                join_values.append(val)
                                if len(join_values) >= 50:
                                    break
                        if join_values:
                            # 匹配参数
                            for p in sig_params:
                                pname = p.get("name", "")
                                if pname in (target_key, join_key):
                                    params[pname] = join_values[0]
                                    break
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

        # 4. 回退：直接匹配当前概念的查询参数
        for p in sig_params:
            pname = p.get("name", "")
            if all_values:
                params[pname] = all_values[0]
                break

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
                "- 编码类值（如 ECN2026-002、MO001）填到对应的编码/编号参数\n"
                "- 无法提取的参数省略，不要输出空字符串\n"
                f"用户消息：{message}\n\n"
                '输出格式：{"参数名": "值"}，如 {"ecnCode": "ECN2026-002"}'
            )
            model = _get_configured_model("decision_model")
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

    def _find_join_keys(self, from_concept: str, to_concept: str) -> tuple:
        """查找两个概念间的 join key。返回 (from_side_key, to_side_key) 或 (None, None)。"""
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

    # 从 DB 加载所有 pipeline 链
    try:
        from app.db import _async_session as _sf
        from app.repositories.chain_repo import ChainRepository

        chains = []
        async with _sf() as session:
            repo = ChainRepository(session)
            all_chains = await repo.list_all()
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
