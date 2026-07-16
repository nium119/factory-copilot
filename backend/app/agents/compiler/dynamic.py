"""动态编排器 — LLM 自主决定多跳查询路径。

当没有预定义链匹配时，LLM 看到 Skill 目录 + 关系图，
自主规划查询顺序，逐步执行，最终汇总。

ReAct 风格: Think → Act → Observe → Think → ...
"""

import asyncio
import json
from typing import AsyncGenerator, Optional

from loguru import logger

from app.agents.compiler.models import CompiledRuntime


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

        # 预定义链参考
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
        parts.append("5. 反问规则：时间模糊且没有具体数字就先问一次。"
                          "一次问清所有缺失信息(时间+指标+范围)，只问一次不要分开问。"
                          "用户给了具体数字就直接执行。")
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
        """动态执行多跳查询。

        产出 (type, content) 元组供 SSE 流式输出。
        type: 'content' (LLM 输出), 'step' (步骤信息), 'done' (完成)
        """
        from app.services.action_executor import action_executor

        context = {"message": message}
        steps_taken = []
        planner_prompt = self.build_planner_prompt()

        summary_produced = False
        for step_num in range(1, self.MAX_STEPS + 1):
            # 构建决策提示词
            decision_prompt = self._build_decision_prompt(
                planner_prompt, message, steps_taken, context, step_num, history_messages
            )

            # LLM 决策: 查询哪个概念 or 汇总
            try:
                decision = await self._llm_decide(
                    decision_prompt, model_name, enable_thinking, session_id
                )
            except Exception as e:
                logger.error(f"[DynamicPlanner] 步骤{step_num}异常: {e}")
                yield ('error', f"动态编排步骤{step_num}失败: {e}")
                break

            if decision["action"] == "ask":
                # 用户问题信息不足，反问确认
                reason = decision.get("reason", "")
                yield ('content', f"\n\n---\n### 需要确认\n\n{reason}")
                yield ('done', json.dumps({"steps_taken": len(steps_taken)}))
                return

            if decision["action"] == "summary":
                summary_produced = True
                # 汇总输出
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
                    continue

                yield ('step', json.dumps({
                    "step": step_num, "action": "query_start",
                    "concept": concept,
                    "description": f"{skill.display_name}: {reason}",
                }, ensure_ascii=False))

                # 执行查询 (API 优先, Neo4j 降级)
                query_ok = False
                tool_name = f"{concept}_query"
                sig = action_executor._sigs.get(tool_name)
                if sig:
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

                    # 查询完成后发送 done 事件更新步骤状态
                    yield ('step', json.dumps({
                        "step": step_num, "action": "query_done",
                        "concept": concept,
                        "description": f"{skill.display_name}: {reason}",
                        "ok": query_ok,
                    }, ensure_ascii=False))
                else:
                    # 无 Neo4j tool, 尝试纯 API
                    try:
                        from app.services.multi_system_backend import multi_system_backend
                        if concept in multi_system_backend._concept_system:
                            params = self._extract_params(message, concept)
                            result = await multi_system_backend.query(concept, params)
                            context[f"{concept}_result"] = result
                            steps_taken.append({
                                "step": step_num, "concept": concept,
                                "label": skill.concept_label, "result": result[:500],
                            })
                            query_ok = True
                    except Exception:
                        pass
                    if not query_ok:
                        context[f"{concept}_result"] = f"[概念 {concept} 无查询工具]"
                        steps_taken.append({
                            "step": step_num, "concept": concept,
                            "label": skill.concept_label, "result": "[无查询工具]",
                        })
                    # 也发 query_done 事件
                    yield ('step', json.dumps({
                        "step": step_num, "action": "query_done",
                        "concept": concept,
                        "description": f"{skill.display_name}: {reason}",
                        "ok": query_ok,
                    }, ensure_ascii=False))

        if not summary_produced and steps_taken:
            yield ('error', f"动态编排未能在{self.MAX_STEPS}步内完成分析，请检查链配置")

        yield ('done', json.dumps({
            "steps_taken": len(steps_taken),
            "max_steps": self.MAX_STEPS,
        }, ensure_ascii=False))

    def _resolve_concept(self, name: str) -> str:
        """中文概念名→英文名映射。LLM 可能输出'工单'而非'WorkOrder'。"""
        # 直接匹配英文
        if name in self._concept_skill_map:
            return name
        # 按 concept_label (显示名) 匹配
        for skill in self.runtime.skills:
            if skill.concept == name or skill.concept_label == name or skill.display_name == name:
                return skill.concept
        # 从 action_executor 的 sigs 中查找
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

        # 注入对话历史（追问上下文，历史消息已由上游截断/摘要）
        if history_messages:
            parts.append("## 对话历史（上文已包含完整上下文，当前问题可能是对历史追问的回答）")
            for hm in history_messages:
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
                    system_prompt="你是一个简洁的决策引擎。信息确实无法执行时才用 ASK:问题。有大致的范围就按默认理解用 QUERY:概念名 执行，不要反复追问。可以总结用 SUMMARY:汇总。",
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
                # 中文名→英文名映射（LLM 可能输出中文概念名）
                resolved = self._resolve_concept(concept)
                from loguru import logger
                logger.info(f"[DynamicPlanner] resolved '{concept}' → '{resolved}'")
                return {"action": "query", "concept": resolved, "reason": reason[:80]}
            else:
                # 默认汇总
                logger.info(f"[DynamicPlanner] 无法解析决策, 默认汇总: {response[:100]}")
                return {"action": "summary"}
        except Exception as e:
            logger.error(f"[DynamicPlanner] LLM 决策失败: {e}")
            return {"action": "summary"}

    async def _llm_summarize(
        self, prompt: str, context: dict,
        model_name: Optional[str], enable_thinking: Optional[bool],
        session_id: str,
    ) -> AsyncGenerator[tuple, None]:
        """LLM 最终汇总。"""
        from app.services.llm_service import llm_service

        summary_prompt = (
            f"{prompt}\n\n"
            f"## 汇总要求\n"
            f"基于以上查询结果输出简洁结论，用表格列出 P0/P1/P2 行动项。不超过200字。"
        )

        async with asyncio.timeout(120):
            async for chunk_type, chunk_content in llm_service.chat_stream(
                message=summary_prompt, session_id=session_id,
                system_prompt="你是制造业分析专家。只输出关键结论和行动项，禁止重复前文。",
                model_name=model_name, enable_thinking=enable_thinking, tools=None,
            ):
                yield (chunk_type, chunk_content)

    def _extract_params(self, message: str, concept_name: str) -> dict:
        """从消息中提取概念查询参数。优先匹配编码格式。"""
        from app.services.intent_router import intent_router
        from app.services.ontology_service import ontology_service
        import re

        tool_name = f"{concept_name}_query"
        params = intent_router.extract_params(message, tool_name)

        # 优先匹配编码格式, 覆盖 intent_router 的误匹配
        m = re.search(r'[A-Z]{2,}[\d-]+', message) or re.search(r'[A-Z]{2,}-\d+(?:-\d+)*', message)
        if m:
            concept = ontology_service.get_concept(concept_name)
            if concept:
                for prop in concept.get("properties", []):
                    if prop.get("isPrimary"):
                        params[prop["name"]] = m.group()
                        break

        if not any(v for v in params.values() if v):
            if m:
                concept = ontology_service.get_concept(concept_name)
                if concept:
                    for prop in concept.get("properties", []):
                        if prop.get("isPrimary"):
                            params[prop["name"]] = m.group()
                            break
        return params
