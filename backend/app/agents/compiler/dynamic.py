"""动态多跳查询规划器 — ReAct 风格 LLM 决策。

查询统一走 action executor，不做二次降级。
"""
import asyncio
import json
import os
import re
from dataclasses import dataclass, field
from typing import Optional, AsyncGenerator

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
        parts.append("5. 仅有在完全无法确定用户意图时才反问(ASK)。有时间范围就用默认理解执行。")
        parts.append("6. 当前消息简短且有对话历史时，是追问回复，提取历史中的完整意图直接执行，不要再次反问。")
        parts.append("")
        parts.append("## 根因分析规则")
        parts.append("- 异常/故障/延期/为什么类问题 → 先查直接对象 → 沿关系逆流追溯上游")
        parts.append("- 每步结果含异常标记(❌/挂起/失败)时，自动查关联上游概念")
        parts.append("- 追溯链: 结果异常 → 查工序 → 查设备 → 查物料 → 查维保 → 查人员")
        parts.append("")
        parts.append("## 输出格式")
        parts.append("如果有歧义或信息不足，先反问: ASK: <需要确认的问题>")
        parts.append("如果需要查询，回复: QUERY: 概念名 (原因, 10字以内)")
        parts.append("如果可以总结，回复: SUMMARY: 汇总内容")

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
                decision = await self._llm_decide(
                    decision_prompt, model_name, enable_thinking, session_id
                )
            except Exception as e:
                logger.error(f"[DynamicPlanner] 步骤{step_num}异常: {e}")
                yield ('error', f"动态编排步骤{step_num}失败: {e}")
                break

            if decision["action"] == "ask":
                reason = decision.get("reason", "")
                yield ('content', f"\n\n---\n### 需要确认\n\n{reason}")
                yield ('done', json.dumps({"steps_taken": len(steps_taken)}))
                return

            if decision["action"] == "summary":
                summary_produced = True
                yield ('step', json.dumps({
                    "step": step_num, "action": "summary",
                    "description": "综合汇总",
                }, ensure_ascii=False))
                yield ('content', f"\n\n---\n### 综合汇总\n\n")
                async for chunk_type, chunk_content in self._llm_summarize(
                    decision_prompt, context, model_name, enable_thinking, session_id
                ):
                    if chunk_type == 'content':
                        yield ('content', chunk_content)
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
                }, ensure_ascii=False))

                # 统一走 action executor，无 sig 时构造最小 sig
                query_ok = False
                tool_name = f"{concept}_query"
                sig = action_executor._sigs.get(tool_name)
                if not sig:
                    sig = {"conceptName": concept, "functionName": tool_name}
                try:
                    params = self._extract_params(message, concept)
                    result = await action_executor._execute_query(sig, params)
                    context[f"{concept}_result"] = result
                    steps_taken.append({
                        "step": step_num, "concept": concept,
                        "label": skill.concept_label, "result": result[:500],
                    })
                    query_ok = True
                except Exception as e:
                    logger.error(f"[DynamicPlanner] 查询失败 {concept}: {e}")
                    context[f"{concept}_result"] = f"[查询失败: {e}]"
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
            yield ('error', f"动态编排未能在{self.MAX_STEPS}步内完成分析，请检查链配置")

        yield ('done', json.dumps({
            "steps_taken": len(steps_taken),
            "max_steps": self.MAX_STEPS,
        }, ensure_ascii=False))

    def _resolve_concept(self, name: str) -> str:
        """中文概念名→英文名映射。LLM 可能输出'工单'而非'WorkOrder'。"""
        if name in self._concept_skill_map:
            return name
        for skill in self.runtime.skills:
            if skill.concept == name or skill.concept_label == name or skill.display_name == name:
                return skill.concept
        from app.services.action_executor import action_executor
        action_executor._ensure_loaded()
        for sig_name in action_executor._sigs:
            sig = action_executor._sigs[sig_name]
            cn = sig.get('conceptName', '')
            cl = sig.get('conceptLabel', '')
            if cn == name or cl == name or f'{cn}查询' == name or f'{cl}查询' == name:
                return cn
        return name

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
                    system_prompt="你是一个简洁的决策引擎。只在完全无法确定用户意图时用 ASK:简短问题（最多一次）。有对话历史时，当前消息是追问回复，直接 QUERY 执行不要反问。有大致的范围就按默认理解用 QUERY:概念名 执行。可以总结用 SUMMARY:汇总。",
                    model_name=model_name or "qwen-turbo",
                    enable_thinking=False,
                    tools=None,
                ):
                    if chunk_type == 'content':
                        response += chunk_content

            response = response.strip()
            if response.startswith("ASK:") or response.startswith("ASK："):
                reason = response.replace("ASK:", "").replace("ASK：", "").strip()
                return {"action": "ask", "reason": reason}
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
                # 剥离 LLM 可能附加的英文名括号: "工单派工(WorkOrderDispatch)" → "工单派工"
                concept = re.sub(r'\([^)]*\)', '', concept).strip()
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
        session_id: str,
    ) -> AsyncGenerator[tuple, None]:
        """流式输出 LLM 汇总总结。"""
        from app.services.llm_service import llm_service

        data_text_parts = []
        for k, v in context.items():
            if k != "message" and v:
                data_text_parts.append(f"### {k}\n{v}")
        data_text = "\n\n".join(data_text_parts)

        msg = context.get('message', '')
        is_anomaly = any(w in msg for w in ('为什么', '原因', '异常', '故障', '延期', '挂起', '分析根因'))
        if is_anomaly:
            logger.info(f"[DynamicPlanner] 根因分析模式, msg={msg[:50]}")
        anomaly_requirement = ("\n## 根因分析要求\n用箭头链展示因果追溯路径，格式：`异常现象 → 直接原因 → 根本原因`。"
                               "\n最后用 Mermaid flowchart 画出因果图：\n```mermaid\nflowchart LR\n  A[现象] --> B[原因] --> C[根因]\n```") if is_anomaly else ""
        summary_prompt = (
            f"## 用户问题\n{msg}\n\n"
            f"## 查询数据\n{data_text}\n\n"
            f"请根据以上数据输出分析结论。数据充分时分层报告（概览→发现→行动）；"
            f"数据不足时简洁总结 + P0/P1/P2 行动项，无数据直接告知。"
            f"{anomaly_requirement}"
        )

        anomaly_sys = "根因分析必须输出 Mermaid flowchart 因果图。" if is_anomaly else ""
        async for chunk_type, chunk_content in llm_service.chat_stream(
            message=summary_prompt, session_id=session_id,
            model_name=model_name or "qwen-turbo",
            enable_thinking=enable_thinking,
            system_prompt="你是制造业数据分析专家。根据数据量自适应：数据多→分层详报，数据少→简洁总结。不编造。" + anomaly_sys,
            tools=None,
        ):
            yield (chunk_type, chunk_content)

    def _extract_params(self, message: str, concept: str) -> dict:
        """从消息中提取查询参数。"""
        params = {}
        from app.services.action_executor import action_executor
        action_executor._ensure_loaded()

        sig = action_executor._sigs.get(f"{concept}_query", {})
        for p in sig.get("parameters", []):
            pname = p.get("name", "")
            # 简单数字提取
            m = re.search(r'(\d{4,})', message)
            if m:
                params[pname] = m.group(1)
                break
        return params
