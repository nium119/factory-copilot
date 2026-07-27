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

    MAX_STEPS = 4

    def __init__(self, runtime: CompiledRuntime):
        self.runtime = runtime
        self._skill_map = {s.name: s for s in runtime.skills}
        self._concept_skill_map = {s.concept: s for s in runtime.skills}

    def build_planner_prompt(self) -> str:
        """构建注入给 LLM 的规划上下文。"""
        parts = [
            "你是制造业智能分析助手。你可以查询以下概念的数据：",
            "",
            self.runtime.skill_catalog_text,
            "",
        ]

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
        parts.append("")
        parts.append("## 根因分析规则（仅问题含为什么/异常/故障/延期/根因时生效）")
        parts.append("- 先查直接对象 → 结果含异常标记(❌/挂起/失败)时 → 沿关系逆流追溯上游")
        parts.append("- 追溯链: 直接对象 → 关联工序/任务 → 关联设备/物料 → 维保/人员")
        parts.append("")
        parts.append("## 输出格式")
        parts.append("如果有歧义或信息不足，先反问: ASK: <需要确认的问题>")
        parts.append("如果需要查询，回复: QUERY: 概念名 原因简述")
        parts.append("如果可以总结，回复: SUMMARY: 汇总内容")
        parts.append("（注意：概念名只能是一个，不要加括号或其他字符）")

        return "\n".join(parts)

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

        context = {"message": message}
        steps_taken = []
        planner_prompt = self.build_planner_prompt()

        summary_produced = False
        for step_num in range(1, self.MAX_STEPS + 1):
            decision_prompt = self._build_decision_prompt(
                planner_prompt, message, steps_taken, context, step_num, history_messages
            )

            try:
                # 决策始终用快速模型，不受前端选择影响
                decision = await self._llm_decide(
                    decision_prompt, None, False, session_id
                )
            except Exception as e:
                logger.error(f"[DynamicPlanner] 步骤{step_num}异常: {e}")
                yield ('error', f"动态编排步骤{step_num}失败: {e}")
                break

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

            elif decision["action"] == "query":
                concept = decision.get("concept", "")
                reason = decision.get("reason", "")
                skill = self._concept_skill_map.get(concept)

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
                try:
                    params = await self._extract_params(message, concept, context, steps_taken)
                    # 使用 _query_via_backend 获取原始记录，供后续跳提取 join key
                    from app.services.data_backend import data_backend as _db
                    result, row_count, _, raw_records = await action_executor._query_via_backend(
                        concept, sig, params, _db,
                    )
                    context[f"{concept}_result"] = result
                    context[f"{concept}_records"] = raw_records
                    steps_taken.append({
                        "step": step_num, "concept": concept,
                        "label": skill.concept_label, "result": result[:500],
                    })
                    query_ok = True
                except Exception as e:
                    logger.error(f"[DynamicPlanner] 查询失败 {concept}: {e}")
                    context[f"{concept}_result"] = f"[查询失败: {e}]"
                    context[f"{concept}_records"] = []
                    steps_taken.append({
                        "step": step_num, "concept": concept,
                        "label": skill.concept_label, "result": f"[错误: {e}]",
                    })

                yield ('step', json.dumps({
                    "step": step_num, "action": "query_done",
                    "concept": concept,
                    "description": f"{skill.display_name}: {reason}",
                    "ok": query_ok,
                    "output_preview": str(context.get(f"{concept}_result", ""))[:2000],
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
                        "消息为闲聊/测试/问候/无数据需求 → SUMMARY 简短回复即可。\n"
                        "单维度不确定（缺时间）→用默认值QUERY。\n"
                        "多维度不确定（缺概念+缺时间）→ASK分组确认。\n"
                        "\n"
                        '示例：「你好」→ SUMMARY:你好！请描述你想查询的数据。\n'
                        '示例：「测试」→ SUMMARY:系统就绪，请描述你的数据查询需求。\n'
                        '示例：「最近情况」→ ASK:您想了解哪方面？|时间:今天,本周,本月|维度:生产进度,质量,设备状态'
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
        summary_prompt = (
            f"## 本体关系说明\n{self.runtime.skill_catalog_text}\n\n"
            f"## 用户问题\n{msg}\n\n"
            f"## 当前日期\n{datetime.now().strftime('%Y-%m-%d %H:%M')}（分析时请以此为准判断时间先后）\n\n"
            f"## 查询数据\n{data_text}\n\n"
            f"请根据以上数据及本体关系输出分析结论。"
            f"注意：不同概念的属性值天然不同（如工单物料是成品料号，BOM物料是组件料号），非异常。"
            f"数据充分时分层报告（概览→发现→行动）；"
            f"数据不足时简洁总结 + P0/P1/P2 行动项，无数据直接告知。"
            f"\n## 可用操作（优先选用，若需其他操作也可提出）"
            f"\n" + "\n".join(
                f"- {s.display_name or s.concept_label}{'的' + a if a != 'query' else '查询'}（{s.concept}_{a}）"
                for s in self.runtime.skills
                for a in (s.actions if hasattr(s, 'actions') and s.actions else ['query'])
            ) + "\n"
            f"\n## 变更方案输出要求"
            f"\n如果分析涉及变更操作（增/删/改/替换/调整），在报告末尾用 ```json 代码块输出变更方案数组："
            f"\n[{{\"id\":\"plan_1\",\"label\":\"方案标题\",\"recommended\":true,\"risk\":\"low|medium|high\","
            f"\n  \"precondition\":\"前提条件\",\"impact\":\"影响说明\","
            f"\n  \"steps_preview\":[\"步骤1\",\"步骤2\"],"
            f"\n  \"actions\":[\"ConceptName_actionName\"],"
            f"\n  \"params_suggestion\":{{\"工单号\":\"MO001\",\"物料编码\":\"380000\"}}}}]"
            f"\n其中："
            f"\n- actions 必须从上方「可用操作」列表中选择，用于后续关联执行链。"
            f"\n- params_suggestion 从查询数据中提取关键参数值（用中文键名），供执行时预填。只填查询数据中明确存在的值，不要猜测。"
            f"\n如无变更需求则不输出此 JSON 块。"
            f"\n## 报告输出规范"
            f"\n### 1. 中文命名"
            f"\n报告中**绝对禁止**出现英文概念名（如 WorkOrder、WorkOrderBOM）和英文属性名（如 materialCode、workOrderCode）。"
            f"\n必须全部使用中文名称，例如：工单BOM、物料编码、工单号、计划数量、开工日期。"
            f"\n概念名参考：「WorkOrder→工单」「WorkOrderBOM→工单BOM」「WorkOrderTask→工单任务」「WorkOrderDispatch→工单派工」"
            f"\n### 2. 排版格式"
            f"\n- 关键数值用**粗体**突出"
            f"\n- 状态用 ✅❌⚠️ 标记"
            f"\n- 数据对比用 Markdown 表格"
            f"\n- 发现和结论用 🔍📊⚠️ 等 emoji 分节"
            f"\n- 行动建议用 P0🔴 / P1🟡 / P2🟢 标记优先级"
            f"\n### 3. 禁用"
            f"\n- 报告中不要出现英文操作名（如 adjustBomQty），这些仅在 JSON 的 actions 字段中使用"
            f"\n- 不要输出英文代码或英文属性名"
            f"{anomaly_requirement}"
        )

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

        # 变更方案：LLM 推导 + 链自动匹配 + 参数提取
        if _llm_plans:
            _plans = await _match_chains_to_plans(_llm_plans)
            # 从查询数据中提取参数值补充到方案
            _params_from_context = _extract_params_from_context(context, steps_taken or [])
            for p in _plans:
                if not p.get("params_suggestion"):
                    p["params_suggestion"] = {}
                # 补充 LLM 没填的参数
                for k, v in _params_from_context.items():
                    if k not in p["params_suggestion"]:
                        p["params_suggestion"][k] = v
            yield ('change_plans', _json.dumps(_plans, ensure_ascii=False))
            logger.info(f"[DynamicPlanner] LLM 推导 {len(_plans)} 个变更方案，{sum(1 for p in _plans if p.get('chain_id'))} 个已匹配链")

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

        # 查询参数优先用编译的运行时 skill（含 conceptPropertyRef），
        # 回退到 action_executor 签名。统一转为 dict 列表。
        skill = self._concept_skill_map.get(concept)
        sig_params = []
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

        # 1. 提取消息中的编码/数字（不用 \\b，中文也是 \\w 会导致边界匹配失败）
        codes = re.findall(r'([A-Z]{2,6}[-_]?\d{2,8})', message)
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

    # 候选映射：概念属性名 → 中文键名
    PARAM_MAP = {
        "code": "工单号", "materialCode": "物料编码", "materialName": "物料名称",
        "quantity": "生产数量", "routingCode": "工艺路线", "endDate": "完工日期",
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
            else:
                plan["chain_id"] = ""
    except Exception as e:
        logger.warning(f"[DynamicPlanner] 链匹配失败: {e}")
        for p in plans:
            p.setdefault("chain_id", "")

    return plans
