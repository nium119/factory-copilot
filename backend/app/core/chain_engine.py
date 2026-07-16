"""本体驱动的链式引擎 — 动态多概念查询规划器。

三阶段执行：
  阶段 1: 查询 Neo4j 获取真实数据（从本体关系中发现的关联概念）
  阶段 2: 链式 LLM 推理步骤（每步看到前序输出 + 阶段 1 数据）
  阶段 3: 最终 LLM 综合分析

链定义（触发条件、推理步骤、最终提示词）存储在 config/chains.yaml。
概念发现和数据查询始终由本体驱动。
"""

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional

from loguru import logger

from app.agents.agent_config import AGENT_DEFINITIONS


def _agent_display(internal_name: str) -> str:
    info = AGENT_DEFINITIONS.get(internal_name, {})
    return info.get("display_name", internal_name)


# ── 数据结构 ──────────────────────────────────────────────────


@dataclass
class ReasoningStep:
    """单个 LLM 推理步骤 — 接收数据上下文 + 前序步骤输出。"""
    step_id: str
    description: str
    agent_name: str
    prompt_template: str
    output_key: str
    focus_concepts: str = ""  # 该步骤查询的概念


@dataclass
class ChainPlan:
    """动态构建的多概念查询 + 推理 + 综合分析计划。"""
    chain_id: str
    name: str
    description: str
    concepts: list = field(default_factory=list)
    relations: list = field(default_factory=list)
    reasoning_steps: list = field(default_factory=list)  # [ReasoningStep, ...]
    final_prompt_template: str = ""
    mode: str = "analysis"  # "analysis" = LLM only, "action" = agent.process() for tool calls


# ── 数据库链注册表 ───────────────────────────────────────────


async def _load_chains_async() -> Dict[str, dict]:
    """从 agent.db 加载链定义（ORM 版本）。"""
    from app.db import get_db
    from app.repositories.chain_repo import ChainRepository
    chains = {}
    try:
        async for session in get_db():
            repo = ChainRepository(session)
            for chain in await repo.get_enabled():
                chains[chain.chain_id] = {
                    "chain_id": chain.chain_id,
                    "name": chain.name or "",
                    "description": chain.description or "",
                    "triggers": json.loads(chain.triggers or "[]"),
                    "final_prompt_template": chain.final_prompt_template or "",
                    "focus_concepts": chain.focus_concepts or "",
                    "reasoning_steps": [
                        {
                            "step_order": s.step_order,
                            "step_id": s.step_id or "",
                            "description": s.description or "",
                            "agent_name": s.agent_name or "",
                            "prompt_template": s.prompt_template or "",
                            "output_key": s.output_key or "",
                            "focus_concepts": s.focus_concepts or "",
                        }
                        for s in (chain.steps or [])
                    ],
                }
    except Exception:
        return {}
    return chains


def _load_chains_from_db() -> Dict[str, dict]:
    """从 agent.db 加载链定义（同步包装）。"""
    from app.db import run_async
    try:
        return run_async(_load_chains_async())
    except Exception:
        return {}


def reload_chains():
    """从数据库重新加载链定义（API 修改后调用）。"""
    global _CHAINS
    _CHAINS = _load_chains_from_db()


_CHAINS: Dict[str, dict] = _load_chains_from_db()


class OntologyChainEngine:
    """本体驱动的链式引擎 — 三阶段执行。"""

    def __init__(self):
        self._agent_resolver: Optional[Callable] = None
        self.last_plan: Optional[ChainPlan] = None
        self._executing: bool = False  # 防递归标志

    # ── 公共接口 ──────────────────────────────────────────────

    def detect(self, message: str) -> Optional[str]:
        """检测消息是否触发多概念分析链。

        返回 chain_id 或 None。执行中跳过防止递归。
        """
        if self._executing:
            return None
        message_lower = message.lower()
        for chain_id, cfg in _CHAINS.items():
            for pattern in cfg.get("triggers", []):
                if re.search(pattern, message_lower):
                    logger.info(f"[ChainEngine] 检测到链: {chain_id} (pattern: {pattern})")
                    return chain_id
        return None

    def set_agent_resolver(self, resolver: Callable):
        self._agent_resolver = resolver

    async def execute(
        self,
        message: str,
        chain_id: str = "",
        model_name: Optional[str] = None,
        enable_thinking: Optional[bool] = None,
        session_id: str = "",
        history_messages: list = None,
    ) -> AsyncGenerator[tuple, None]:
        """执行三阶段本体驱动链式分析。

        产出 (type, content) 元组供 SSE 流式输出。
        chain_id 由调用方传入，避免重复 detect 引发递归。
        """
        if not self._agent_resolver:
            logger.error("[ChainEngine] Agent 解析器未设置")
            yield ('error', '链式引擎未正确初始化')
            return

        if not chain_id:
            chain_id = self.detect(message)
        if not chain_id:
            # 无预定义链匹配 → 尝试动态编排
            runtime = self._get_compiled_runtime()
            if runtime and runtime.skills:
                logger.info("[ChainEngine] 无链匹配, 启用动态编排")
                async for chunk in self._execute_dynamic(
                    message, model_name, enable_thinking, session_id
                ):
                    yield chunk
                return
            yield ('error', '未检测到匹配的分析链')
            return

        self._executing = True
        try:
            plan = await self._build_plan(chain_id, message)
        except Exception:
            self._executing = False
            raise
        self.last_plan = plan

        # ── 发送 chain_start ──
        steps_summary = []
        if plan.reasoning_steps:
            # 链式模式：每步自己管理数据查询，只发送推理步骤
            steps_summary += [
                {"step_id": rs.step_id, "description": rs.description, "phase": "reasoning",
                 "agent_name": rs.agent_name, "focus_concepts": rs.focus_concepts}
                for rs in plan.reasoning_steps
            ]
            # 如果有最终汇总提示词，添加汇总步骤
            if plan.final_prompt_template:
                steps_summary.append(
                    {"step_id": "final_summary", "description": "综合汇总", "phase": "summary"}
                )
        else:
            # 合并模式：先发送数据查询步骤，再发送综合研判步骤
            steps_summary += [
                {"step_id": f"query_{cn}", "description": f"查询{cl}", "phase": "data", "concept": cn}
                for cn, cl, _ in plan.concepts
            ]
            steps_summary.append(
                {"step_id": "comprehensive_analysis", "description": "综合研判", "phase": "reasoning"}
            )
        yield ('chain_start', json.dumps({
            "chain_id": plan.chain_id,
            "chain_name": plan.name,
            "steps": steps_summary,
            "relations": [
                {"source": s, "label": l, "target": t}
                for s, l, t in plan.relations
            ],
        }, ensure_ascii=False))
        logger.info(
            f"[ChainEngine] chain_start: {plan.chain_id}, "
            f"{len(plan.concepts)} 个数据查询 + {len(plan.reasoning_steps)} 个推理步骤"
        )

        # ═══════════════════════════════════════════════════════
        # 阶段 1: 查询 Neo4j 获取真实数据（仅合并模式；链式模式每步独立查询）
        # ═══════════════════════════════════════════════════════
        from app.services.action_executor import action_executor

        data_sections: Dict[str, str] = {}
        if not plan.reasoning_steps:
            # 合并模式：查询链级 focus_concepts
            for idx, (cn, cl, tool_name) in enumerate(plan.concepts):
                yield ('chain_step', json.dumps({
                    "step_id": f"query_{cn}",
                    "status": "running",
                    "description": f"查询{cl}",
                    "phase": "data",
                    "concept": cn,
                }, ensure_ascii=False))
                logger.info(f"[ChainEngine] 阶段 1 查询: {cn} via {tool_name}")

                try:
                    sig = action_executor._sigs.get(tool_name)
                    if sig:
                        params = self._extract_params_for_concept(message, cn)
                        result = await action_executor._execute_query(sig, params)
                        data_sections[cn] = result
                        yield ('chain_step', json.dumps({
                            "step_id": f"query_{cn}",
                            "status": "done",
                            "description": f"查询{cl}",
                            "phase": "data",
                            "concept": cn,
                            "output_preview": result[:200] + ("..." if len(result) > 200 else ""),
                        }, ensure_ascii=False))
                    else:
                        data_sections[cn] = f"[无查询工具] {cn}"
                        yield ('chain_step', json.dumps({
                            "step_id": f"query_{cn}",
                            "status": "error",
                            "phase": "data",
                            "error": f"概念 {cn} 没有查询 Action",
                        }, ensure_ascii=False))
                except Exception as e:
                    logger.error(f"[ChainEngine] 阶段 1 查询失败 {cn}: {e}")
                    data_sections[cn] = f"[查询失败] {e}"
                    yield ('chain_step', json.dumps({
                        "step_id": f"query_{cn}",
                        "status": "error",
                        "phase": "data",
                        "error": str(e),
                    }, ensure_ascii=False))

        # 为推理步骤构建数据上下文字符串
        data_text_parts = []
        for cn, cl, _ in plan.concepts:
            data = data_sections.get(cn, "[无数据]")
            data_text_parts.append(f"## {cl} ({cn})\n\n{data}")
        data_context = "\n\n".join(data_text_parts) if data_text_parts else ""

        # ═══════════════════════════════════════════════════════
        # 阶段 2: 推理
        # - 如果 reasoning_steps 为空且 final_prompt_template 存在 → 合并为一次 LLM 调用
        # - 否则 → 逐步执行 reasoning_steps（action chain 需要 agent.process()）
        # ═══════════════════════════════════════════════════════
        if plan.mode == "action":
            # ── Action 模式：agent.process() 执行工具链 ──
            for rs in plan.reasoning_steps:
                yield ('chain_step', json.dumps({
                    "step_id": rs.step_id, "status": "running",
                    "description": rs.description, "phase": "reasoning",
                    "agent_name": rs.agent_name,
                }, ensure_ascii=False))
                agent = self._agent_resolver(rs.agent_name)
                if agent:
                    async for chunk_type, chunk_content in agent.process(
                        message=rs.prompt_template.replace("{message}", message).replace("{data_context}", data_context),
                        session_id=session_id, model_name=model_name,
                        use_agent=False, web_search=False, enable_thinking=enable_thinking,
                        context=None, history_messages=history_messages or [], matched_agents=[],
                    ):
                        if chunk_type == 'content':
                            yield ('content', chunk_content)
                yield ('chain_step', json.dumps({
                    "step_id": rs.step_id, "status": "done",
                    "description": rs.description, "phase": "reasoning",
                }, ensure_ascii=False))
            reasoning_ok = len(plan.reasoning_steps)
            total_steps = len(plan.reasoning_steps)
            summary_ok = 0

        elif not plan.reasoning_steps and plan.final_prompt_template:
            # ── 合并模式：一次 LLM 综合研判 ──
            yield ('chain_step', json.dumps({
                "step_id": "comprehensive_analysis",
                "status": "running",
                "description": "综合研判",
                "phase": "reasoning",
            }, ensure_ascii=False))
            yield ('content', "\n\n---\n### 综合研判\n\n")
            logger.info("[ChainEngine] 阶段 2 综合研判（合并模式）")

            try:
                from app.services.llm_service import llm_service
                from datetime import datetime as _dt
                _today = _dt.now().strftime("%Y-%m-%d")
                analysis_prompt = (plan.final_prompt_template
                    .replace("{message}", message)
                    .replace("{data_context}", data_context))
                # 注入今日日期，强制 LLM 按要求时间过滤
                analysis_prompt = (
                    f"【当前日期: {_today}】\n"
                    f"报告日期写 {_today}，只使用 {_today} 的数据。"
                    f"如果无 {_today} 数据，只需回复：今日（{_today}）无生产数据\n\n"
                    + analysis_prompt
                )
                # 无数据时注入诚实指令，防止 LLM 编造分析内容
                if data_sections and all(v.startswith("未找到") for v in data_sections.values()):
                    analysis_prompt = (
                        "⚠️ 未查询到任何匹配的实时数据。直接一句话告知用户无数据，"
                        "提示用户提供具体查询条件。禁止输出分析框架或评估模板。回复不超过3句话。\n\n" + analysis_prompt
                    )
                async with asyncio.timeout(120):
                    async for chunk_type, chunk_content in llm_service.chat_stream(
                        message=analysis_prompt,
                        session_id=session_id,
                        system_prompt="你是数据分析专家。直接输出 Markdown 格式报告（表格+图表+行动项），不要用 ```markdown 或 ```md 代码块包裹输出。图表用 ```echarts 代码块生成柱状图/饼图。",
                        model_name=model_name,
                        enable_thinking=enable_thinking,
                        tools=None,
                    ):
                        if chunk_type == 'content':
                            yield ('content', chunk_content)

                yield ('chain_step', json.dumps({
                    "step_id": "comprehensive_analysis",
                    "status": "done", "description": "综合研判", "phase": "reasoning",
                }, ensure_ascii=False))
            except asyncio.TimeoutError:
                yield ('chain_step', json.dumps({
                    "step_id": "comprehensive_analysis", "status": "error",
                    "phase": "reasoning", "error": "推理超时",
                }, ensure_ascii=False))
            except Exception as e:
                logger.error(f"[ChainEngine] 综合研判失败: {e}")
                yield ('chain_step', json.dumps({
                    "step_id": "comprehensive_analysis", "status": "error",
                    "phase": "reasoning", "error": str(e),
                }, ensure_ascii=False))
            reasoning_ok = 1
            total_steps = len(plan.concepts) + 1
            summary_ok = 0
        else:
            # ── 链式模式：每步独立查询数据集 + 逐步推理 ──
            context: Dict[str, str] = {"message": message}
            for rs in plan.reasoning_steps:
                yield ('chain_step', json.dumps({
                    "step_id": rs.step_id, "status": "running",
                    "description": rs.description, "phase": "reasoning",
                    "agent_name": rs.agent_name,
                    "agent_display_name": _agent_display(rs.agent_name),
                }, ensure_ascii=False))
                logger.info(f"[ChainEngine] 链式推理: {rs.step_id} → {rs.agent_name}")

                # 查询该步骤专属数据
                step_data_parts = []
                data_found = False
                if rs.focus_concepts:
                    from app.services.ontology_service import ontology_service
                    _cmap = {c["name"]: c for c in (ontology_service.get_concepts() or [])}
                    step_concepts = [c.strip() for c in rs.focus_concepts.split(",") if c.strip()]
                    for cn in step_concepts:
                        tool_name = f"{cn}_query"
                        sig = action_executor._sigs.get(tool_name)
                        if sig:
                            try:
                                params = self._extract_params_for_concept(message, cn)
                                result = await action_executor._execute_query(sig, params)
                                label = _cmap.get(cn, {}).get("label", cn)
                                step_data_parts.append(f"## {label} ({cn})\n\n{result}")
                                if not result.startswith("未找到"):
                                    data_found = True
                            except Exception as e:
                                logger.warning(f"[ChainEngine] 查询 {cn} 失败: {e}")
                step_data = "\n\n".join(step_data_parts) or data_context  # 回退到全局数据
                context["data_context"] = step_data

                prompt = rs.prompt_template
                for key, value in context.items():
                    prompt = prompt.replace(f"{{{key}}}", value)

                # 无数据时注入诚实指令，防止 LLM 编造分析内容
                if not data_found and rs.focus_concepts:
                    prompt = (
                        "⚠️ 未查询到任何匹配的实时数据。直接一句话告知用户无数据，"
                        "提示用户提供具体查询条件（如工单号、设备编号）。"
                        "禁止输出任何分析框架、评估模板或示例格式。回复不超过3句话。\n\n" + prompt
                    )

                # 步骤标题分段
                yield ('content', f"\n\n---\n### {rs.description}\n\n")
                try:
                    from app.services.llm_service import llm_service
                    step_response = ""
                    async with asyncio.timeout(120):
                        async for chunk_type, chunk_content in llm_service.chat_stream(
                            message=prompt, session_id=session_id,
                            system_prompt="你是制造业专家。用最少的字输出结论。不要写框架、模板或示例。如果没数据就直说。",
                            model_name=model_name, enable_thinking=enable_thinking, tools=None,
                        ):
                            if chunk_type in ('content', 'thinking'):
                                if chunk_type == 'content':
                                    step_response += chunk_content
                                yield (chunk_type, chunk_content)
                    context[rs.output_key] = step_response
                    yield ('chain_step', json.dumps({
                        "step_id": rs.step_id, "status": "done",
                        "description": rs.description, "phase": "reasoning",
                    }, ensure_ascii=False))
                except asyncio.TimeoutError:
                    context[rs.output_key] = "[超时]"
                    yield ('chain_step', json.dumps({
                        "step_id": rs.step_id, "status": "error",
                        "phase": "reasoning", "error": "推理超时",
                    }, ensure_ascii=False))
                except Exception as e:
                    logger.error(f"[ChainEngine] 推理失败 {rs.step_id}: {e}")
                    context[rs.output_key] = f"[错误] {str(e)}"
                    yield ('chain_step', json.dumps({
                        "step_id": rs.step_id, "status": "error",
                        "phase": "reasoning", "error": str(e),
                    }, ensure_ascii=False))

            # 最终汇总（链式模式每步推理后汇总，合并模式在 comprehensive_analysis 已完成）
            summary_ok = 0
            if plan.final_prompt_template:
                yield ('chain_step', json.dumps({
                    "step_id": "final_summary", "status": "running",
                    "description": "综合汇总", "phase": "summary",
                }, ensure_ascii=False))
                yield ('content', "\n\n---\n")
                try:
                    final_prompt = plan.final_prompt_template
                    for key, value in context.items():
                        final_prompt = final_prompt.replace(f"{{{key}}}", value)
                    from datetime import datetime as _dt2
                    _today2 = _dt2.now().strftime("%Y-%m-%d")
                    final_prompt = (
                        f"【当前日期: {_today2}】报告日期写 {_today2}。"
                        f"无 {_today2} 数据就回复：今日（{_today2}）无生产数据\n\n"
                        + final_prompt
                    )
                    async with asyncio.timeout(120):
                        async for chunk_type, chunk_content in llm_service.chat_stream(
                            message=final_prompt, session_id=session_id,
                            system_prompt="你是数据分析专家。直接输出 Markdown 格式报告，不要用 ```markdown 或 ```md 代码块包裹输出。图表用 ```echarts 代码块。",
                            model_name=model_name, enable_thinking=enable_thinking, tools=None,
                        ):
                            if chunk_type == 'content':
                                yield ('content', chunk_content)
                    yield ('chain_step', json.dumps({
                        "step_id": "final_summary", "status": "done",
                        "description": "综合汇总", "phase": "summary",
                    }, ensure_ascii=False))
                    summary_ok = 1
                except asyncio.TimeoutError:
                    yield ('chain_step', json.dumps({
                        "step_id": "final_summary", "status": "error",
                        "phase": "summary", "error": "汇总超时",
                    }, ensure_ascii=False))
                except Exception as e:
                    logger.error(f"[ChainEngine] 最终汇总失败: {e}")
                    yield ('chain_step', json.dumps({
                        "step_id": "final_summary", "status": "error",
                        "phase": "summary", "error": str(e),
                    }, ensure_ascii=False))

            reasoning_ok = sum(1 for rs in plan.reasoning_steps
                if not (context.get(rs.output_key, "") or "").startswith(("[错误]", "[超时]")))
            total_steps = len(plan.reasoning_steps) + (1 if plan.final_prompt_template else 0)

        # ── 发送 chain_done ──
        data_ok = sum(1 for v in data_sections.values() if not v.startswith("["))
        try:
            yield ('chain_done', json.dumps({
                "chain_id": plan.chain_id,
                "steps_completed": data_ok + reasoning_ok + summary_ok,
                "total_steps": total_steps,
                "data_queries": data_ok,
                "reasoning_steps": reasoning_ok,
                "summary_ok": summary_ok,
            }, ensure_ascii=False))
            logger.info(f"[ChainEngine] chain_done: {plan.chain_id} ({data_ok + reasoning_ok}/{total_steps})")
        finally:
            self._executing = False

    # ── 动态编排 ─────────────────────────────────────────────

    @staticmethod
    def _get_compiled_runtime():
        """获取编译器产出 (供动态编排使用)。"""
        try:
            from app.agents import get_compiled_runtime
            return get_compiled_runtime()
        except Exception:
            return None

    async def _execute_dynamic(
        self, message: str, model_name: str = None,
        enable_thinking: bool = None, session_id: str = "",
    ) -> AsyncGenerator[tuple, None]:
        """动态编排: LLM 自主决定多跳查询路径。"""
        from app.agents.compiler.dynamic import DynamicPlanner

        runtime = self._get_compiled_runtime()
        if not runtime:
            yield ('error', '编译器未产出, 动态编排不可用')
            return

        planner = DynamicPlanner(runtime)

        # 发送动态编排开始
        yield ('chain_start', json.dumps({
            "chain_id": "dynamic",
            "chain_name": "智能分析",
            "steps": [],  # 步骤由 LLM 动态决定
            "dynamic": True,
        }, ensure_ascii=False))

        try:
            async for chunk_type, chunk_content in planner.execute(
                message=message, model_name=model_name,
                enable_thinking=enable_thinking, session_id=session_id,
            ):
                if chunk_type == 'step':
                    step = json.loads(chunk_content) if isinstance(chunk_content, str) else chunk_content
                    # 作为 chain_step 事件转发
                    yield ('chain_step', json.dumps({
                        "step_id": f"dynamic_{step.get('step', 1)}",
                        "status": "running" if step.get('action') == 'query' else "done",
                        "description": step.get('description', ''),
                        "phase": "reasoning",
                    }, ensure_ascii=False))
                elif chunk_type == 'content':
                    yield ('content', chunk_content)
                elif chunk_type == 'done':
                    done = json.loads(chunk_content) if isinstance(chunk_content, str) else chunk_content
                    yield ('chain_done', json.dumps({
                        "chain_id": "dynamic",
                        "steps_completed": done.get("steps_taken", 0),
                        "total_steps": done.get("steps_taken", 0),
                        "data_queries": done.get("steps_taken", 0),
                        "reasoning_steps": 1,
                        "dynamic": True,
                    }, ensure_ascii=False))
        except Exception as e:
            logger.error(f"[ChainEngine] 动态编排失败: {e}")
            yield ('error', f'动态编排失败: {e}')

    # ── 计划构建 ─────────────────────────────────────────────

    async def _build_plan(self, chain_id: str, message: str) -> ChainPlan:
        """从本体关系 + YAML 配置动态构建查询计划。

        1. 查找消息中提到的概念
        2. 通过本体关系发现关联概念（1 跳）
        3. 为有 _query Action 的概念构建查询步骤
        4. 从 config/chains.yaml 加载推理步骤
        """
        from app.services.ontology_service import ontology_service
        from app.services.action_executor import action_executor

        action_executor._ensure_loaded()
        concepts = ontology_service.get_concepts()
        concept_map = {c["name"]: c for c in concepts}

        # 查找消息中提到的概念，通过本体关系发现关联概念（限制数量）
        all_names: set[str] = set()
        relations: list[tuple] = []

        # 优先用链配置的数据集
        chain_cfg = _CHAINS.get(chain_id, {})
        focus = chain_cfg.get("focus_concepts", "")
        if focus:
            all_names = set(focus.replace(" ", "").split(","))
        else:
            # 无配置时，LLM 提取消息中的概念
            mentioned = self._find_mentioned_concepts(message, concepts)
            for c in mentioned[:5]:
                all_names.add(c["name"])
                for rel in c.get("relations", [])[:3]:
                    target = rel["target"]
                    if target in concept_map and len(all_names) < 12:
                        all_names.add(target)
                        relations.append((
                            c.get("label", c["name"]),
                            rel.get("label", ""),
                            concept_map[target].get("label", target),
                        ))
            # 仍然为空 → 全量兜底
            if not all_names:
                all_names = set(concept_map.keys())

        # 构建查询概念列表
        query_concepts = []
        for cn in all_names:
            tool_name = f"{cn}_query"
            if tool_name in action_executor._sigs:
                c = concept_map.get(cn, {})
                query_concepts.append((cn, c.get("label", cn), tool_name))

        # 从 YAML 配置加载推理步骤
        chain_cfg = _CHAINS.get(chain_id, {})
        reasoning_steps = [
            ReasoningStep(
                step_id=rs["step_id"],
                description=rs.get("description", ""),
                agent_name=rs.get("agent_name", "analysis_monitor"),
                prompt_template=rs.get("prompt_template", ""),
                output_key=rs.get("output_key", ""),
                focus_concepts=rs.get("focus_concepts", ""),
            )
            for rs in chain_cfg.get("reasoning_steps", [])
        ]

        return ChainPlan(
            chain_id=chain_id,
            name=chain_cfg.get("name", chain_id),
            description=chain_cfg.get("description", ""),
            concepts=query_concepts,
            relations=relations,
            reasoning_steps=reasoning_steps,
            final_prompt_template=chain_cfg.get("final_prompt_template", ""),
            mode=chain_cfg.get("mode", "analysis"),
        )

    def _find_mentioned_concepts(self, message: str, concepts: list) -> list:
        """查找用户消息中提及的本体概念。"""
        mentioned = []
        for c in concepts:
            label = c.get("label", "")
            name = c.get("name", "")
            desc = c.get("description", "")
            if label and label in message:
                mentioned.append(c)
            elif name and name.lower() in message.lower():
                mentioned.append(c)
            elif desc:
                for kw in self._extract_keywords(desc):
                    if kw in message and len(kw) >= 2:
                        mentioned.append(c)
                        break
        return mentioned

    @staticmethod
    def _extract_keywords(text: str) -> set:
        if not text:
            return set()
        kw = set()
        for part in re.split(r'[，。、；：（）\s]+', text):
            part = part.strip()
            if len(part) >= 2:
                kw.add(part)
        return kw

    def _extract_params_for_concept(self, message: str, concept_name: str) -> dict:
        """从消息中提取概念查询的过滤参数。"""
        from app.services.intent_router import intent_router
        from app.services.ontology_service import ontology_service

        tool_name = f"{concept_name}_query"
        params = intent_router.extract_params(message, tool_name)

        # 优先匹配编码格式 (MO001, WO-20250521-001) → 覆盖 intent_router 的误匹配
        m = re.search(r'[A-Z]{2,}[\d-]+', message) or re.search(r'[A-Z]{2,}-\d+(?:-\d+)*', message)
        if m:
            concept = ontology_service.get_concept(concept_name)
            if concept:
                for prop in concept.get("properties", []):
                    if prop.get("isPrimary"):
                        params[prop["name"]] = m.group()
                        break

        # intent_router 没提取到参数时也尝试编码匹配
        if not any(v for v in params.values() if v):
            if m:
                concept = ontology_service.get_concept(concept_name)
                if concept:
                    for prop in concept.get("properties", []):
                        if prop.get("isPrimary"):
                            params[prop["name"]] = m.group()
                            break

        return params


# 全局单例
chain_engine = OntologyChainEngine()
