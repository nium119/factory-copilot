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

import os as _os
import sqlite3 as _sqlite3

_DB_PATH = _os.path.join(_os.path.dirname(__file__), "..", "..", "data", "agent.db")


def _load_chains_from_db() -> Dict[str, dict]:
    """从 agent.db 加载链定义。"""
    if not _os.path.exists(_DB_PATH):
        return {}
    conn = _sqlite3.connect(_DB_PATH)
    conn.row_factory = _sqlite3.Row
    try:
        c = conn.cursor()
        c.execute("SELECT * FROM chains WHERE enabled = 1")
        chains = {}
        for row in c.fetchall():
            r = dict(row)
            chain_id = r["chain_id"]
            r["triggers"] = json.loads(r.get("triggers", "[]"))
            r["reasoning_steps"] = []
            r["focus_concepts"] = r.get("focus_concepts", "")
            chains[chain_id] = r
        c.execute("SELECT * FROM chain_steps ORDER BY chain_id, step_order")
        for row in c.fetchall():
            rs = dict(row)
            chain_id = rs.pop("chain_id")
            rs.pop("id", None)
            if chain_id in chains:
                chains[chain_id]["reasoning_steps"].append(rs)
        return chains
    except Exception:
        return {}
    finally:
        conn.close()


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
        steps_summary += [
            {"step_id": f"query_{cn}", "description": f"查询{cl}", "phase": "data", "concept": cn}
            for cn, cl, _ in plan.concepts
        ]
        if plan.mode == "action":
            steps_summary += [
                {"step_id": rs.step_id, "description": rs.description, "phase": "reasoning",
                 "agent_name": rs.agent_name}
                for rs in plan.reasoning_steps
            ]
        else:
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
        # 阶段 1: 查询 Neo4j 获取真实数据
        # ═══════════════════════════════════════════════════════
        from app.services.action_executor import action_executor

        data_sections: Dict[str, str] = {}
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
        data_context = "\n\n".join(data_text_parts)

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
            total_steps = len(plan.concepts) + len(plan.reasoning_steps)

        elif not plan.reasoning_steps and plan.final_prompt_template:
            # ── 合并模式：一次 LLM 综合研判 ──
            yield ('chain_step', json.dumps({
                "step_id": "comprehensive_analysis",
                "status": "running",
                "description": "综合研判",
                "phase": "reasoning",
            }, ensure_ascii=False))
            logger.info("[ChainEngine] 阶段 2 综合研判（合并模式）")

            try:
                from app.services.llm_service import llm_service
                analysis_prompt = plan.final_prompt_template.replace("{message}", message).replace("{data_context}", data_context)
                async with asyncio.timeout(120):
                    async for chunk_type, chunk_content in llm_service.chat_stream(
                        message=analysis_prompt,
                        session_id=session_id,
                        system_prompt="你是制造业绩效分析专家。基于KPI数据输出结构化报告。只输出表格和行动项。不输出解释性正文。",
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
        else:
            # ── 逐步模式：执行 reasoning_steps + final_prompt（action chain）──
            context: Dict[str, str] = {"message": message, "data_context": data_context}
            for rs in plan.reasoning_steps:
                yield ('chain_step', json.dumps({
                    "step_id": rs.step_id, "status": "running",
                    "description": rs.description, "phase": "reasoning",
                    "agent_name": rs.agent_name,
                    "agent_display_name": _agent_display(rs.agent_name),
                }, ensure_ascii=False))
                logger.info(f"[ChainEngine] 阶段 2 推理: {rs.step_id} → {rs.agent_name}")

                prompt = rs.prompt_template
                for key, value in context.items():
                    prompt = prompt.replace(f"{{{key}}}", value)

                try:
                    from app.services.llm_service import llm_service
                    step_response = ""
                    async with asyncio.timeout(120):
                        async for chunk_type, chunk_content in llm_service.chat_stream(
                            message=prompt, session_id=session_id,
                            system_prompt="你是制造业专家。输出简洁的结构化分析，不写冗长正文。",
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

            # Final prompt synthesis
            if plan.final_prompt_template and plan.final_agent:
                final_prompt = plan.final_prompt_template
                for key, value in context.items():
                    final_prompt = final_prompt.replace(f"{{{key}}}", value)
                async for chunk_type, chunk_content in llm_service.chat_stream(
                    message=final_prompt, session_id=session_id,
                    system_prompt="你是制造业分析专家。",
                    model_name=model_name, enable_thinking=enable_thinking, tools=None,
                ):
                    if chunk_type == 'content':
                        yield ('content', chunk_content)

            reasoning_ok = sum(1 for rs in plan.reasoning_steps
                if not (context.get(rs.output_key, "") or "").startswith(("[错误]", "[超时]")))
            total_steps = len(plan.concepts) + len(plan.reasoning_steps)

        # ── 发送 chain_done ──
        data_ok = sum(1 for v in data_sections.values() if not v.startswith("["))
        yield ('chain_done', json.dumps({
            "chain_id": plan.chain_id,
            "steps_completed": data_ok + reasoning_ok,
            "total_steps": total_steps,
            "data_queries": data_ok,
            "reasoning_steps": reasoning_ok,
        }, ensure_ascii=False))
        logger.info(f"[ChainEngine] chain_done: {plan.chain_id} ({data_ok + reasoning_ok}/{total_steps})")
        self._executing = False

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

        mentioned = self._find_mentioned_concepts(message, concepts)
        for c in mentioned[:5]:  # 最多 5 个核心概念
            all_names.add(c["name"])
            for rel in c.get("relations", [])[:3]:  # 每个概念最多 3 条关系
                target = rel["target"]
                if target in concept_map and len(all_names) < 12:  # 总共最多 12 个概念
                    all_names.add(target)
                    relations.append((
                        c.get("label", c["name"]),
                        rel.get("label", ""),
                        concept_map[target].get("label", target),
                    ))

        if not all_names:
            # 检查链配置是否指定了核心概念
            chain_cfg = _CHAINS.get(chain_id, {})
            focus = chain_cfg.get("focus_concepts", "")
            if focus:
                all_names = set(focus.split(","))
            else:
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

        tool_name = f"{concept_name}_query"
        params = intent_router.extract_params(message, tool_name)

        if not any(v for v in params.values() if v):
            m = re.search(r'[A-Z]{2,}-\d+(?:-\d+)*', message)
            if m:
                from app.services.ontology_service import ontology_service
                concept = ontology_service.get_concept(concept_name)
                if concept:
                    for prop in concept.get("properties", []):
                        if prop.get("isPrimary"):
                            params[prop["name"]] = m.group()
                            break

        return params


# 全局单例
chain_engine = OntologyChainEngine()
